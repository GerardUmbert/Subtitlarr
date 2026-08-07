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

_API_BASE = "https://api.groq.com/openai/v1"

# Confirmed via https://console.groq.com/docs/rate-limits for the default
# model (llama-3.1-8b-instant), free tier: 30 requests/minute, 14,400
# requests/day — Groq's most generous documented free-tier limits; larger
# models on Groq typically get a LOWER per-model cap, so check
# console.groq.com/docs/rate-limits if the configured model is changed.
# Both numbers are far more generous than OpenRouter's free-tier ":free"
# models (20 RPM / 50 per day) — the whole reason this provider was added
# was OpenRouter's free Gemma being slow AND tightly capped. Groq's own
# selling point is LPU inference (not GPU), so this should also just be
# faster in wall-clock terms, not only quota terms.
RATE_LIMIT_RPM = 30
FREE_TIER_DAILY_LIMIT = 14400

# Batches can involve generating many translated cues in one response —
# this is our own httpx CLIENT-side wait limit, not anything Groq
# imposes. Matches NVIDIA/OpenRouter/Ollama's 600s default for the same
# reason: a large batch can legitimately take minutes to generate.
DEFAULT_GROQ_TIMEOUT_SECONDS = 600.0


class GroqProvider(TranslationProvider):
    """Groq, reached via its OpenAI-compatible /openai/v1/chat/completions
    endpoint. Groq serves a fixed lineup of open-source models (Llama,
    etc. — see console.groq.com/docs/models) on its own LPU inference
    hardware rather than routing to third-party providers; the configured
    model string MUST be a real instructable chat model, same requirement
    as every other provider here. Reuses the exact same system/user
    prompt scheme as Ollama/Gemini/NVIDIA/OpenRouter and does no internal
    re-chunking of its own — batching is handled entirely by the caller's
    existing chunk_cues() token-budget logic."""

    name = "groq"
    provider_type = "groq"

    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.1-8b-instant",
        timeout: float = DEFAULT_GROQ_TIMEOUT_SECONDS,
        instance_name: str | None = None,
    ):
        if instance_name:
            self.name = instance_name
        self._api_key = api_key
        self._model = model
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=_API_BASE,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        # Shared across every translate() call on this ONE provider
        # instance, same pattern as NvidiaProvider/OpenRouterProvider — a
        # 429 here sets "now + cooldown" so every other in-flight batch/
        # item waits at this gate instead of piling more requests onto an
        # exhausted window.
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
        european_spanish: bool = True,
    ) -> str:
        await self._wait_for_rate_limit_clear()
        system_prompt = build_system_prompt(
            source_lang, target_lang, catalan_vegeta_insults, european_spanish
        )
        try:
            resp = await self._client.post(
                "/chat/completions",
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": build_user_prompt(dialogue_text)},
                    ],
                },
            )
        except httpx.TimeoutException as exc:
            raise ProviderRateLimitedError(f"Groq request timed out: {exc}") from exc
        except httpx.ConnectError as exc:
            raise ProviderRateLimitedError(f"Groq connection failed: {exc}") from exc

        if resp.status_code == 429:
            # Groq's documented convention (console.groq.com/docs/rate-limits):
            # Retry-After is only set on a real 429, in seconds. Prefer it;
            # otherwise wait a bit over the RPM window rather than landing
            # right on its boundary. Sets the SHARED gate so every other
            # batch/item on this provider instance waits too.
            retry_after = resp.headers.get("Retry-After")
            wait_seconds = float(retry_after) if retry_after else 62.0
            self._rate_limited_until = time.monotonic() + wait_seconds
            raise ProviderRateLimitedError(
                "Groq rate limit hit (429)", retry_after_seconds=wait_seconds
            )
        if resp.status_code >= 500:
            # Transient server-side failure — retryable, same as a rate
            # limit, rather than a hard failure.
            raise ProviderRateLimitedError(f"Groq server error ({resp.status_code}): {resp.text}")
        if resp.status_code != 200:
            raise ProviderError(f"Groq request failed ({resp.status_code}): {resp.text}")

        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"Unexpected Groq response shape: {data}") from exc

    async def test_connection(self) -> ProviderStatus:
        try:
            resp = await self._client.post(
                "/chat/completions",
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": "Say 'ok'."},
                    ],
                    "max_tokens": 8,
                },
            )
        except httpx.HTTPError as exc:
            return ProviderStatus(ok=False, detail=str(exc))
        if resp.status_code != 200:
            return ProviderStatus(ok=False, detail=f"HTTP {resp.status_code}: {resp.text}")
        return ProviderStatus(ok=True, detail="API key valid")
