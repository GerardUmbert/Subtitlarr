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

_API_BASE = "https://integrate.api.nvidia.com/v1"

# NVIDIA's free-tier NIM account allows up to 40 requests/minute (confirmed
# on the account dashboard) — not actively throttled here since a real
# instructable chat model needs far fewer requests per item than Riva's
# translate-only endpoint did (one request per outer chunk_cues() batch,
# not one per cue).
RATE_LIMIT_RPM = 40

# A large batch (see app.config.Settings.nvidia_batch_token_budget) means
# a single request can involve generating many hundreds of translated
# cues — this is our own httpx CLIENT-side wait limit, not anything NVIDIA
# imposes; a shorter one here just means giving up on a request that may
# still be working. The previous default of 120s was never load-tested
# against a real large batch and confirmed live to be too short: three
# separate real episodes each failed with "NVIDIA request timed out" at
# ~120s, back-to-back, strongly suggesting the requests were still in
# stuck. Matches Ollama's DEFAULT_OLLAMA_TIMEOUT_SECONDS (600s) — same
# reasoning applies (a large batch can legitimately take minutes to
# generate), though NVIDIA's cloud model has no local watchdog/force-unload
# mechanism to pair with it (that's an Ollama-specific concept, since only
# Ollama holds the model loaded in local VRAM between requests).
DEFAULT_NVIDIA_TIMEOUT_SECONDS = 600.0


class NvidiaProvider(TranslationProvider):
    """NVIDIA NIM-hosted chat model (default: DeepSeek V4 Flash), reached
    via an OpenAI-compatible /v1/chat/completions endpoint. This provider
    ONLY supports real instructable chat models — it reuses the exact same
    system/user prompt scheme as Ollama/Gemini (see app.providers.prompts)
    and does no internal re-chunking of its own; batching is handled
    entirely by the caller's existing chunk_cues() token-budget logic.

    A prior version of this provider pointed at NVIDIA's Riva Translate
    model — a dedicated translation-only endpoint with no instructable
    system prompt. It required extensive workarounds (per-request character
    caps, internal re-chunking, "[N]" marker hacks to stop it from merging
    joined lines into continuous prose) and was still unreliable at any
    real batch size (confirmed live: even 5-cue chunks intermittently
    collapsed). Switching to an instructable chat model (confirmed live
    with DeepSeek V4 Flash: a 5-line EN->CA batch came back with all 5
    numbered lines intact, fluent Catalan) removed the need for all of
    that — any model configured here MUST be a real chat model that can
    follow the numbered-index formatting instructions, the same
    requirement Ollama/Gemini already have."""

    name = "nvidia"
    provider_type = "nvidia"

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-ai/deepseek-v4-flash",
        timeout: float = DEFAULT_NVIDIA_TIMEOUT_SECONDS,
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
        # instance — the same instance is reused for a whole run (see
        # registry.get_active_provider), across every item and every
        # concurrent batch window, since NVIDIA's 40 req/min ceiling is
        # per API key/account, not per item or per batch. When any request
        # hits a 429, this is set to "now + cooldown", and every OTHER
        # request (already-running ones excluded — they're left to finish
        # naturally) waits here before firing, instead of each one
        # independently hitting its own 429 and retrying in an
        # uncoordinated pile-up.
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
            raise ProviderRateLimitedError(f"NVIDIA request timed out: {exc}") from exc
        except httpx.ConnectError as exc:
            raise ProviderRateLimitedError(f"NVIDIA connection failed: {exc}") from exc

        if resp.status_code == 429:
            # A 429 means the per-minute request ceiling was hit — retrying
            # immediately (or after only a few seconds) would almost
            # certainly hit another 429, since the window hasn't rolled
            # over yet. Prefer the server's own Retry-After header when
            # present; otherwise wait a bit over a minute (not exactly
            # 60s) — landing right on the window boundary risks a second
            # 429 from clock skew or the request's own latency eating into
            # the margin. Sets the SHARED gate so every other batch (this
            # item's remaining ones, and every later item's) waits too —
            # one account-wide rate limit, not something a single batch
            # can wait out alone while its siblings keep firing into the
            # same wall.
            retry_after = resp.headers.get("Retry-After")
            wait_seconds = float(retry_after) if retry_after else 62.0
            self._rate_limited_until = time.monotonic() + wait_seconds
            raise ProviderRateLimitedError(
                "NVIDIA rate limit hit (429)", retry_after_seconds=wait_seconds
            )
        if resp.status_code >= 500:
            # Transient server-side failure on NVIDIA's own infrastructure
            # (confirmed live: 504 Gateway Timeout and 529 Site Overloaded,
            # both while NVIDIA's backend was clearly struggling, not
            # anything wrong with the request itself) — retryable, same as
            # a rate limit, rather than a hard failure.
            raise ProviderRateLimitedError(
                f"NVIDIA server error ({resp.status_code}): {resp.text}"
            )
        if resp.status_code != 200:
            raise ProviderError(f"NVIDIA request failed ({resp.status_code}): {resp.text}")

        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"Unexpected NVIDIA response shape: {data}") from exc

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
