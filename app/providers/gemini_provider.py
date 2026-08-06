import asyncio
import time

import httpx

from app.providers.base import (
    ProviderError,
    ProviderRateLimitedError,
    ProviderStatus,
    TranslationProvider,
)
from app.providers.prompts import build_system_prompt, build_user_prompt

# REST-only client (no google-generativeai SDK) to avoid that SDK's grpc
# dependency, which has inconsistent musl/alpine wheel availability — see
# the Dockerfile risk notes.
_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

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

    def __init__(self, api_key: str, model: str, timeout: float = DEFAULT_GEMINI_TIMEOUT_SECONDS):
        self._api_key = api_key
        self._model = model
        self._client = httpx.AsyncClient(base_url=_API_BASE, timeout=timeout)
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
    ) -> str:
        await self._wait_for_rate_limit_clear()
        system_prompt = build_system_prompt(source_lang, target_lang, catalan_vegeta_insults)
        try:
            resp = await self._client.post(
                f"/models/{self._model}:generateContent",
                params={"key": self._api_key},
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
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"Unexpected Gemini response shape: {data}") from exc

    async def test_connection(self) -> ProviderStatus:
        try:
            resp = await self._client.get("/models", params={"key": self._api_key})
        except httpx.HTTPError as exc:
            return ProviderStatus(ok=False, detail=str(exc))
        if resp.status_code != 200:
            return ProviderStatus(ok=False, detail=f"HTTP {resp.status_code}: {resp.text}")
        return ProviderStatus(ok=True, detail="API key valid")
