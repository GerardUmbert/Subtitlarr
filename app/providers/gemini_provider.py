import asyncio
import json
import logging
import time

import httpx

from app.providers.base import (
    ProviderContentBlockedError,
    ProviderError,
    ProviderRateLimitedError,
    ProviderStatus,
    TranslationProvider,
)
from app.providers.prompts import build_system_prompt, build_user_prompt

logger = logging.getLogger(__name__)

# REST-only client (no google-generativeai SDK) to avoid that SDK's grpc
# dependency, which has inconsistent musl/alpine wheel availability — see
# the Dockerfile risk notes.
_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Every documented value of promptFeedback.blockReason (the whole prompt
# was rejected before generation started) and candidate.finishReason (the
# response was blocked/cut short after generation started) — per
# ai.google.dev/api/generate-content. Mapped to a short, human-readable
# explanation instead of showing the raw enum name, which by itself
# doesn't tell a user what to actually do about it. Confirmed live: real
# failures returned PROHIBITED_CONTENT (not SAFETY, which is what an
# earlier version of this file only checked for) — this table exists so
# adding the next reason Google introduces is one line, not another
# missed-case bug like that one.
_BLOCK_REASON_EXPLANATIONS = {
    "SAFETY": "flagged by Gemini's safety filter",
    "PROHIBITED_CONTENT": "flagged as prohibited content (explicit/mature material, etc.)",
    "BLOCKLIST": "contains a blocklisted term",
    "IMAGE_SAFETY": "flagged by Gemini's image-safety filter",
    "OTHER": "blocked for an unspecified reason",
}
# finishReason values that mean "no usable output" for OUR purposes —
# STOP/MAX_TOKENS are normal-ish outcomes (a real response exists, even
# if truncated) and are handled by the normal candidates[0].content.parts
# lookup below, not by this table.
_FINISH_REASON_EXPLANATIONS = {
    "SAFETY": "flagged by Gemini's safety filter",
    "PROHIBITED_CONTENT": "flagged as prohibited content (explicit/mature material, etc.)",
    "RECITATION": "withheld for suspected copyrighted-content recitation",
    "SPII": "withheld for containing sensitive personal information",
    "BLOCKLIST": "contains a blocklisted term",
    "OTHER": "blocked for an unspecified reason",
}

# Unlike NVIDIA/Groq (which document a fixed RPM) or OpenRouter (which
# documents both RPM and a daily cap), Google's own docs
# (ai.google.dev/gemini-api/docs/rate-limits) explicitly do NOT publish a
# fixed free-tier number — actual limits depend on account/usage tier and
# must be checked per-account in AI Studio. So this provider reacts to
# whatever 429 the account actually gets rather than pre-emptively
# throttling against a guessed number, same posture NVIDIA/OpenRouter take
# for anything beyond their own confirmed ceilings.
DEFAULT_GEMINI_TIMEOUT_SECONDS = 600.0


class GeminiProvider(TranslationProvider):
    """Google Gemini, reached via its REST generateContent endpoint.
    Reuses the exact same system/user prompt scheme as Ollama/NVIDIA/
    OpenRouter/Groq and does no internal re-chunking of its own —
    batching is handled entirely by the caller's existing chunk_cues()
    token-budget logic, same as every other cloud provider here."""

    name = "gemini"
    provider_type = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = DEFAULT_GEMINI_TIMEOUT_SECONDS,
        instance_name: str | None = None,
    ):
        if instance_name:
            self.name = instance_name
        self._api_key = api_key
        self._model = model
        self.model = model
        # The key goes in the x-goog-api-key HEADER, not a ?key= query
        # param — Google's REST API accepts both, but a query param ends
        # up in every httpx/uvicorn access log line verbatim (confirmed
        # live: the full key was visible in plaintext in server.log with
        # the old params={"key": ...} approach). The header form keeps it
        # out of logged URLs entirely.
        self._client = httpx.AsyncClient(
            base_url=_API_BASE, timeout=timeout, headers={"x-goog-api-key": api_key}
        )
        # Shared across every translate() call on this ONE provider
        # instance, same pattern as NvidiaProvider/OpenRouterProvider/
        # GroqProvider — a 429 here sets "now + cooldown" so every other
        # in-flight batch/item waits at this gate instead of piling more
        # requests onto an exhausted window.
        self._rate_limited_until: float = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _wait_for_rate_limit_clear(self) -> None:
        remaining = self._rate_limited_until - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(remaining)

    async def translate(
        self,
        dialogue_text: str,
        source_lang: str,
        target_lang: str,
        catalan_vegeta_insults: bool = False,
        language_variants: dict[str, str] | None = None,
    ) -> str:
        await self._wait_for_rate_limit_clear()
        system_prompt = build_system_prompt(
            source_lang, target_lang, catalan_vegeta_insults, language_variants
        )
        try:
            resp = await self._client.post(
                f"/models/{self._model}:generateContent",
                json={
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": [
                        {"role": "user", "parts": [{"text": build_user_prompt(dialogue_text)}]}
                    ],
                },
            )
        except httpx.TimeoutException as exc:
            raise ProviderRateLimitedError(f"Gemini request timed out: {exc}") from exc
        except httpx.ConnectError as exc:
            raise ProviderRateLimitedError(f"Gemini connection failed: {exc}") from exc

        if resp.status_code == 429:
            # Google's docs don't specify a Retry-After header for this
            # endpoint, so fall back to a flat wait a bit over a minute —
            # same fallback NVIDIA/OpenRouter/Groq use when no header is
            # present. Sets the SHARED gate so every other batch/item on
            # this provider instance waits too.
            #
            # The response BODY is logged here (not just the bare status
            # code) — confirmed live this matters: a real run saw a ~76%
            # 429 rate against an account whose own AI Studio dashboard
            # showed plenty of RPM/TPM/RPD headroom, and with no body
            # logged there was no way to tell whether that was a genuine
            # per-minute limit, a tighter undocumented per-second burst
            # limit, or something else Google's error body would actually
            # name (e.g. a QuotaFailure violation with a specific metric).
            logger.warning("Gemini 429 response body: %s", resp.text)
            retry_after = resp.headers.get("Retry-After")
            wait_seconds = float(retry_after) if retry_after else 62.0
            self._rate_limited_until = time.monotonic() + wait_seconds
            raise ProviderRateLimitedError(
                "Gemini rate limit hit (429)", retry_after_seconds=wait_seconds
            )
        if resp.status_code >= 500:
            # Transient server-side failure — retryable, same as a rate
            # limit, rather than a hard failure.
            raise ProviderRateLimitedError(
                f"Gemini server error ({resp.status_code}): {resp.text}"
            )
        if resp.status_code != 200:
            raise ProviderError(f"Gemini request failed ({resp.status_code}): {resp.text}")

        data = resp.json()

        # A blocked request still comes back as HTTP 200 with no
        # candidates/parts at all — confirmed live: multiple items in
        # real runs failed with a bare "KeyError: 'parts'"/"KeyError:
        # 'candidates'" and no useful detail, because the response was
        # one of Gemini's block shapes, not a malformed success response.
        # Detected per ai.google.dev/api/generate-content:
        # promptFeedback.blockReason means the whole prompt was rejected
        # before generation even started; a candidate's finishReason
        # means generation started but the output itself was withheld.
        # The short message (str(exc), shown directly in the Queue/
        # History tables) stays human-readable via the explanation
        # tables above; raw_detail carries the full response JSON for a
        # "show full error" expansion, since the raw shape (safetyRatings,
        # finishMessage, token counts, etc.) is genuinely useful for
        # diagnosing WHY something tripped the filter, just too long/
        # technical for a table cell.
        block_reason = data.get("promptFeedback", {}).get("blockReason")
        if block_reason:
            explanation = _BLOCK_REASON_EXPLANATIONS.get(
                block_reason, f"blocked ({block_reason})"
            )
            raise ProviderContentBlockedError(
                f"Gemini blocked this request — {explanation}. Not a batch-size or "
                "rate-limit issue on THIS provider — falling back to a different "
                "engine may still succeed.",
                raw_detail=json.dumps(data, indent=2),
            )
        candidates = data.get("candidates") or []
        finish_reason = candidates[0].get("finishReason") if candidates else None
        if finish_reason in _FINISH_REASON_EXPLANATIONS:
            explanation = _FINISH_REASON_EXPLANATIONS[finish_reason]
            finish_message = candidates[0].get("finishMessage")
            summary = f"Gemini blocked its own response — {explanation}."
            if finish_message:
                summary += f" {finish_message}"
            raise ProviderContentBlockedError(
                f"{summary} Not a batch-size or rate-limit issue on THIS provider — "
                "falling back to a different engine may still succeed.",
                raw_detail=json.dumps(data, indent=2),
            )
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(
                "Unexpected Gemini response shape — see full error for the raw response.",
                raw_detail=json.dumps(data, indent=2),
            ) from exc

    async def test_connection(self) -> ProviderStatus:
        try:
            resp = await self._client.get("/models")
        except httpx.HTTPError as exc:
            return ProviderStatus(ok=False, detail=str(exc))
        if resp.status_code != 200:
            return ProviderStatus(ok=False, detail=f"HTTP {resp.status_code}: {resp.text}")
        return ProviderStatus(ok=True, detail="API key valid")
