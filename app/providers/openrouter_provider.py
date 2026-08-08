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

_API_BASE = "https://openrouter.ai/api/v1"

# Confirmed via https://openrouter.ai/docs/api_reference/limits: free model
# variants (model id ends in ":free") are capped at 20 requests/minute
# REGARDLESS of account credit, plus a per-day cap (50/day under $10 in
# purchased credits, 1000/day at $10+). Same role as NVIDIA's
# RATE_LIMIT_RPM — not actively throttled ahead of time (reacting to a
# real 429 is simpler and correct either way), just documented here so the
# batch/concurrency defaults below aren't picked blind.
RATE_LIMIT_RPM = 20
FREE_TIER_DAILY_LIMIT = 50

# Batches can involve generating many translated cues in one response —
# this is our own httpx CLIENT-side wait limit, not anything OpenRouter
# imposes. Matches NVIDIA/Ollama's 600s default for the same reason: a
# large batch can legitimately take minutes to generate.
DEFAULT_OPENROUTER_TIMEOUT_SECONDS = 600.0


class OpenRouterDailyLimitError(ProviderError):
    """The account's daily request cap (see FREE_TIER_DAILY_LIMIT) was hit.
    Deliberately NOT a ProviderRateLimitedError: a per-minute 429 clears in
    seconds and is worth retrying, but the daily cap only resets at the
    provider's own daily boundary — retrying within the same run would
    just burn the retry budget on a wait that won't pay off. The runner's
    per-item exception handling treats this as a hard failure for that
    item, same as any other ProviderError."""


class OpenRouterProvider(TranslationProvider):
    """OpenRouter, reached via its OpenAI-compatible /v1/chat/completions
    endpoint. OpenRouter is a router in front of many different underlying
    models (OpenAI, Anthropic, Meta, etc. — see openrouter.ai/models), so
    the configured model string MUST be a real instructable chat model;
    this provider reuses the exact same system/user prompt scheme as
    Ollama/Gemini/NVIDIA and does no internal re-chunking of its own —
    batching is handled entirely by the caller's existing chunk_cues()
    token-budget logic."""

    name = "openrouter"
    provider_type = "openrouter"

    def __init__(
        self,
        api_key: str,
        model: str = "google/gemma-4-26b-a4b-it:free",
        timeout: float = DEFAULT_OPENROUTER_TIMEOUT_SECONDS,
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
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        # Shared across every translate() call on this ONE provider
        # instance, same pattern as NvidiaProvider — a 429 here sets
        # "now + cooldown" so every other in-flight batch/item waits at
        # this gate instead of piling more requests onto an exhausted
        # window.
        self._rate_limited_until: float = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _wait_for_rate_limit_clear(self) -> None:
        remaining = self._rate_limited_until - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(remaining)

    def _is_daily_limit(self, resp: httpx.Response) -> bool:
        # OpenRouter's documented 429 body: {"error": {"code": 429,
        # "message": "...", "metadata": {"error_type": "..."}}}. A daily
        # cap hit reports this distinctly from a plain per-minute
        # throttle, but the exact error_type string isn't guaranteed
        # stable, so also fall back to sniffing "day"/"daily" in the
        # message text.
        try:
            body = resp.json()
        except ValueError:
            return False
        error = body.get("error", {}) if isinstance(body, dict) else {}
        error_type = str(error.get("metadata", {}).get("error_type", "")).lower()
        message = str(error.get("message", "")).lower()
        return "daily" in error_type or "daily" in message or "per day" in message

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
        return await self._chat_completion([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": build_user_prompt(dialogue_text)},
        ])

    async def ask(self, prompt: str) -> str:
        """No system prompt — the caller's text is the entire message.
        Used by app.engine.language_check's batched language audit, not
        translate()'s subtitle-specific prompt scheme."""
        return await self._chat_completion([{"role": "user", "content": prompt}])

    async def _chat_completion(self, messages: list[dict]) -> str:
        await self._wait_for_rate_limit_clear()
        body = {"model": self._model, "messages": messages}
        if self._temperature is not None:
            body["temperature"] = self._temperature
        try:
            resp = await self._client.post("/chat/completions", json=body)
        except httpx.TimeoutException as exc:
            raise ProviderRateLimitedError(f"OpenRouter request timed out: {exc}") from exc
        except httpx.ConnectError as exc:
            raise ProviderRateLimitedError(f"OpenRouter connection failed: {exc}") from exc

        if resp.status_code == 429:
            if self._is_daily_limit(resp):
                # Not retryable within this run — no wait will make the
                # daily quota reappear. Raising a plain ProviderError (via
                # this subclass) means the runner treats it as a hard
                # per-item failure instead of looping retries against a
                # wall that won't move until the provider's next day.
                raise OpenRouterDailyLimitError(
                    "OpenRouter daily free-tier request limit reached "
                    f"(free tier: {FREE_TIER_DAILY_LIMIT}/day) — "
                    "wait for the daily reset or add credits at openrouter.ai/credits"
                )
            # Per-minute throttle: prefer the server's own Retry-After
            # header when present, then X-RateLimit-Reset (ms since epoch,
            # OpenRouter's own convention per the docs), otherwise wait a
            # bit over the RPM window rather than landing right on its
            # boundary. Sets the SHARED gate so every other batch/item on
            # this provider instance waits too.
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                wait_seconds = float(retry_after)
            elif resp.headers.get("X-RateLimit-Reset"):
                reset_ms = float(resp.headers["X-RateLimit-Reset"])
                wait_seconds = max(0.0, reset_ms / 1000 - time.time())
            else:
                wait_seconds = 62.0
            self._rate_limited_until = time.monotonic() + wait_seconds
            raise ProviderRateLimitedError(
                "OpenRouter rate limit hit (429)", retry_after_seconds=wait_seconds
            )
        if resp.status_code >= 500:
            # Transient server-side failure — retryable, same as a rate
            # limit, rather than a hard failure.
            raise ProviderRateLimitedError(
                f"OpenRouter server error ({resp.status_code}): {resp.text}"
            )
        if resp.status_code != 200:
            raise ProviderError(f"OpenRouter request failed ({resp.status_code}): {resp.text}")

        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"Unexpected OpenRouter response shape: {data}") from exc

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
