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

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-ai/deepseek-v4-flash",
        timeout: float = DEFAULT_NVIDIA_TIMEOUT_SECONDS,
    ):
        self._api_key = api_key
        self._model = model
        self._client = httpx.AsyncClient(
            base_url=_API_BASE,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def translate(self, dialogue_text: str, source_lang: str, target_lang: str) -> str:
        system_prompt = build_system_prompt(source_lang, target_lang)
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
            raise ProviderRateLimitedError(f"NVIDIA request timed out: {exc}") from exc
        except httpx.ConnectError as exc:
            raise ProviderRateLimitedError(f"NVIDIA connection failed: {exc}") from exc

        if resp.status_code == 429:
            raise ProviderRateLimitedError("NVIDIA rate limit hit (429)")
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
