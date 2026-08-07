import asyncio
import logging

import httpx

from app.providers.base import (
    ProviderError,
    ProviderRateLimitedError,
    ProviderStatus,
    TranslationProvider,
)
from app.providers.prompts import build_system_prompt, build_user_prompt

logger = logging.getLogger(__name__)

# Same reasoning as Ollama's DEFAULT_OLLAMA_TIMEOUT_SECONDS: a real local
# translation request can legitimately take minutes for a large batch on
# consumer hardware, so this needs real margin over normal-case latency
# without ballooning into a many-minutes ceiling that masks a genuinely
# stuck request.
DEFAULT_LLAMACPP_TIMEOUT_SECONDS = 600.0

# Watchdog, same role as Ollama's WATCHDOG_TIMEOUT_SECONDS: llama.cpp's
# server has no equivalent to Ollama's keep_alive=0 force-unload (there is
# no "evict model" endpoint — the model is loaded once at server startup
# via CLI args, not per-request), so a wedged request here can only be
# broken by cancelling client-side and retrying against the same
# already-running server — there's nothing to force-unload.
WATCHDOG_TIMEOUT_SECONDS = 300.0


class LlamaCppProvider(TranslationProvider):
    """A local llama.cpp server (the project's own built-in HTTP server —
    see github.com/ggml-org/llama.cpp/tools/server — not Ollama, a
    separate local inference runtime), reached via its OpenAI-compatible
    /v1/chat/completions endpoint. llama.cpp's server has no web UI: it is
    a headless HTTP server only, started with a specific model already
    loaded via CLI flags — there is no equivalent to Ollama's /api/pull or
    a model-switching endpoint, so unlike OllamaProvider this provider has
    no pull_model()/list_models().

    `model` is optional and, when set, sent in the request body — most
    llama.cpp server builds ignore it entirely (only one model is ever
    loaded), but some builds/versions, and any reverse proxy in front
    (LiteLLM, etc.) enforcing strict OpenAI-spec requests, reject a
    request with no `model` field at all. Confirmed live: a friend's
    llama.cpp instance behind a Tailscale Funnel returned a real 400
    "model name is missing from the request" with no `model` sent.
    Leave blank against a server that doesn't require it.

    Local-only like Ollama: no per-minute/per-day rate limit to react to,
    and no windowed concurrency (see translator._CONCURRENT_PROVIDERS) —
    concurrent requests would just serialize against the same loaded
    model on the same GPU/CPU anyway, same reasoning as Ollama.

    llama.cpp's own server has no built-in auth either, but — same
    situation as Ollama — a remote instance can sit behind a reverse
    proxy/gateway that enforces its own (confirmed live: the same
    Tailscale-Funnel instance above, gated by a bearer token in front of
    it). api_key is optional and only sent as an Authorization header
    when set."""

    name = "llamacpp"
    provider_type = "llamacpp"

    def __init__(
        self,
        base_url: str,
        timeout: float = DEFAULT_LLAMACPP_TIMEOUT_SECONDS,
        api_key: str | None = None,
        model: str | None = None,
        instance_name: str | None = None,
    ):
        if instance_name:
            self.name = instance_name
        self._base_url = base_url.rstrip("/")
        self._model = model
        self.model = model or "(server default)"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout, headers=headers)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _chat_request(self, system_prompt: str, dialogue_text: str) -> httpx.Response:
        body = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": build_user_prompt(dialogue_text)},
            ],
        }
        if self._model:
            body["model"] = self._model
        return await self._client.post("/v1/chat/completions", json=body)

    async def translate(
        self,
        dialogue_text: str,
        source_lang: str,
        target_lang: str,
        catalan_vegeta_insults: bool = False,
        european_spanish: bool = True,
    ) -> str:
        system_prompt = build_system_prompt(
            source_lang, target_lang, catalan_vegeta_insults, european_spanish
        )

        for attempt in (1, 2):
            try:
                resp = await asyncio.wait_for(
                    self._chat_request(system_prompt, dialogue_text),
                    timeout=WATCHDOG_TIMEOUT_SECONDS,
                )
                break
            except asyncio.TimeoutError:
                if attempt == 2:
                    raise ProviderRateLimitedError(
                        f"llama.cpp request still stuck after {WATCHDOG_TIMEOUT_SECONDS:.0f}s "
                        "on retry — giving up."
                    )
                logger.warning(
                    "llama.cpp request exceeded watchdog timeout (%.0fs) with no response; "
                    "retrying once (no force-unload equivalent — the server has no "
                    "per-request model-eviction endpoint like Ollama's keep_alive=0).",
                    WATCHDOG_TIMEOUT_SECONDS,
                )
            except httpx.TimeoutException as exc:
                raise ProviderRateLimitedError(f"llama.cpp request timed out: {exc}") from exc
            except httpx.ConnectError as exc:
                raise ProviderRateLimitedError(f"llama.cpp connection failed: {exc}") from exc

        if resp.status_code == 429:
            raise ProviderRateLimitedError("llama.cpp returned 429")
        if resp.status_code != 200:
            raise ProviderError(f"llama.cpp request failed ({resp.status_code}): {resp.text}")

        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"Unexpected llama.cpp response shape: {data}") from exc
        if not content:
            raise ProviderError(f"llama.cpp response missing message content: {data}")
        return content

    async def test_connection(self) -> ProviderStatus:
        try:
            resp = await self._client.get("/health")
        except httpx.HTTPError as exc:
            return ProviderStatus(ok=False, detail=str(exc))
        if resp.status_code == 503:
            return ProviderStatus(ok=False, detail="Server is loading the model — try again shortly")
        if resp.status_code != 200:
            return ProviderStatus(ok=False, detail=f"HTTP {resp.status_code}")

        # /health doesn't report which model is loaded — /v1/models does,
        # so surface that in the success detail (best-effort: some server
        # builds/versions may not expose it, don't fail the whole check
        # over a missing extra).
        model_name = None
        try:
            models_resp = await self._client.get("/v1/models")
            if models_resp.status_code == 200:
                models = models_resp.json().get("data", [])
                if models:
                    model_name = models[0].get("id")
        except httpx.HTTPError:
            pass

        detail = f"responded, model '{model_name}' loaded" if model_name else "responded"
        return ProviderStatus(ok=True, detail=detail)
