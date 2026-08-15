import asyncio
import time

import httpx

from app.providers.base import (
    ProviderAuthError,
    ProviderContentBlockedError,
    ProviderError,
    ProviderRateLimitedError,
    ProviderStatus,
    TranslationProvider,
)
from app.providers.prompts import build_system_prompt, build_user_prompt

_API_BASE = "https://api.anthropic.com/v1"
_API_VERSION = "2023-06-01"

# Batches can involve generating many translated cues in one response —
# this is our own httpx CLIENT-side wait limit, not anything Anthropic
# imposes. Matches Gemini/NVIDIA/OpenRouter/Groq's 600s default for the
# same reason: a large batch can legitimately take minutes to generate.
DEFAULT_ANTHROPIC_TIMEOUT_SECONDS = 600.0

# Unlike the OpenAI-compatible providers here, Anthropic's Messages API
# REQUIRES max_tokens on every request — there's no "unbounded" option.
# Sized well above the largest batch_token_budget used elsewhere (Gemini/
# OpenRouter's 4000) since translated output can run longer than the
# source due to the '<index>\ntext' formatting overhead repeated per cue.
DEFAULT_MAX_TOKENS = 8192


class AnthropicProvider(TranslationProvider):
    """Anthropic Claude, reached via its Messages API
    (api.anthropic.com/v1/messages). Reuses the exact same system/user
    prompt scheme as Ollama/Gemini/NVIDIA/OpenRouter/Groq and does no
    internal re-chunking of its own — batching is handled entirely by the
    caller's existing chunk_cues() token-budget logic, same as every other
    cloud provider here."""

    name = "anthropic"
    provider_type = "anthropic"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = DEFAULT_ANTHROPIC_TIMEOUT_SECONDS,
        temperature: float | None = None,
        instance_name: str | None = None,
    ):
        if instance_name:
            self.name = instance_name
        self._api_key = api_key
        self._model = model
        self.model = model
        self._temperature = temperature
        self._client = httpx.AsyncClient(
            base_url=_API_BASE,
            timeout=timeout,
            headers={
                "x-api-key": api_key,
                "anthropic-version": _API_VERSION,
            },
        )
        # Shared across every translate() call on this ONE provider
        # instance, same pattern as NvidiaProvider/OpenRouterProvider/
        # GroqProvider/GeminiProvider — a 429 here sets "now + cooldown" so
        # every other in-flight batch/item waits at this gate instead of
        # piling more requests onto an exhausted window.
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
        system_prompt = build_system_prompt(
            source_lang, target_lang, catalan_vegeta_insults, language_variants
        )
        return await self._create_message(
            system=system_prompt,
            messages=[{"role": "user", "content": build_user_prompt(dialogue_text)}],
        )

    async def ask(self, prompt: str) -> str:
        """No system prompt — the caller's text is the entire message.
        Used by app.engine.language_check's batched language audit, not
        translate()'s subtitle-specific prompt scheme."""
        return await self._create_message(
            system=None, messages=[{"role": "user", "content": prompt}]
        )

    async def _create_message(self, system: str | None, messages: list[dict]) -> str:
        await self._wait_for_rate_limit_clear()
        body = {
            "model": self._model,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "messages": messages,
        }
        if system is not None:
            body["system"] = system
        if self._temperature is not None:
            body["temperature"] = self._temperature
        try:
            resp = await self._client.post("/messages", json=body)
        except httpx.TimeoutException as exc:
            raise ProviderRateLimitedError(f"Anthropic request timed out: {exc}") from exc
        except httpx.ConnectError as exc:
            raise ProviderRateLimitedError(f"Anthropic connection failed: {exc}") from exc

        if resp.status_code == 429:
            # Anthropic documents anthropic-ratelimit-*-reset headers (unix
            # timestamps) rather than a plain Retry-After — falls back to
            # the same flat ~62s wait every other provider here uses when
            # no usable header is present. Sets the SHARED gate so every
            # other batch/item on this provider instance waits too.
            retry_after = resp.headers.get("retry-after")
            wait_seconds = float(retry_after) if retry_after else 62.0
            self._rate_limited_until = time.monotonic() + wait_seconds
            raise ProviderRateLimitedError(
                "Anthropic rate limit hit (429)", retry_after_seconds=wait_seconds
            )
        if resp.status_code == 529:
            # Anthropic-specific "overloaded" status — transient, retryable,
            # same treatment as a 5xx.
            raise ProviderRateLimitedError(f"Anthropic overloaded (529): {resp.text}")
        if resp.status_code >= 500:
            raise ProviderRateLimitedError(
                f"Anthropic server error ({resp.status_code}): {resp.text}"
            )
        if resp.status_code in (401, 403):
            # A bad/revoked key will never self-resolve by retrying —
            # see ProviderAuthError's docstring.
            raise ProviderAuthError(f"Anthropic request failed ({resp.status_code}): {resp.text}")
        if resp.status_code != 200:
            raise ProviderError(f"Anthropic request failed ({resp.status_code}): {resp.text}")

        data = resp.json()

        # stop_reason == "refusal" means Claude declined to generate a
        # response for this content — not a rate limit, outage, or
        # malformed request, so this maps to the same fallback-eligible-
        # but-not-same-provider-retryable bucket Gemini's content-block
        # detection uses. See ProviderContentBlockedError's docstring.
        if data.get("stop_reason") == "refusal":
            raise ProviderContentBlockedError(
                "Claude declined to translate this content. Not a batch-size or "
                "rate-limit issue on THIS provider — falling back to a different "
                "engine may still succeed.",
                raw_detail=str(data),
            )
        try:
            return data["content"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(
                "Unexpected Anthropic response shape — see full error for the raw response.",
                raw_detail=str(data),
            ) from exc

    async def test_connection(self) -> ProviderStatus:
        try:
            resp = await self._client.post(
                "/messages",
                json={
                    "model": self._model,
                    "max_tokens": 8,
                    "messages": [{"role": "user", "content": "Say 'ok'."}],
                },
            )
        except httpx.HTTPError as exc:
            return ProviderStatus(ok=False, detail=str(exc))
        if resp.status_code != 200:
            return ProviderStatus(ok=False, detail=f"HTTP {resp.status_code}: {resp.text}")
        return ProviderStatus(ok=True, detail="API key valid")
