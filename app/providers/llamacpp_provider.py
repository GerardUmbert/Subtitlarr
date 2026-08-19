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
# consumer hardware. Set well above WATCHDOG_TIMEOUT_SECONDS (below) so the
# watchdog always gets a chance to cancel and retry a stuck request BEFORE
# this outer timeout would otherwise cut the whole attempt short.
DEFAULT_LLAMACPP_TIMEOUT_SECONDS = 1200.0

# Watchdog, same role as Ollama's WATCHDOG_TIMEOUT_SECONDS: llama.cpp's
# server has no equivalent to Ollama's keep_alive=0 force-unload (there is
# no "evict model" endpoint — the model is loaded once at server startup
# via CLI args, not per-request), so a wedged request here can only be
# broken by cancelling client-side and retrying against the same
# already-running server — there's nothing to force-unload.
WATCHDOG_TIMEOUT_SECONDS = 600.0


def _resolve_reasoning_effort(thinking: bool | str) -> str:
    """Maps the shared bool|"low"|"medium"|"high" thinking setting onto
    llama.cpp server's own reasoning_effort values. False -> "none"
    (matches Ollama's think=False default-off behavior); True has no
    direct llama.cpp equivalent (it's an Ollama on/off shorthand), so it
    maps to "medium" as a reasonable middle default."""
    if thinking is False:
        return "none"
    if thinking is True:
        return "medium"
    return thinking


class LlamaCppProvider(TranslationProvider):
    """A local llama.cpp server (the project's own built-in HTTP server —
    see github.com/ggml-org/llama.cpp/tools/server — not Ollama, a
    separate local inference runtime), reached via its OpenAI-compatible
    /v1/chat/completions endpoint. llama.cpp's server has no web UI: it is
    a headless HTTP server only, started with a specific model already
    loaded via CLI flags — there is no equivalent to Ollama's /api/pull or
    a model-switching endpoint, so unlike OllamaProvider this provider has
    no pull_model(). It DOES have list_models() (via /v1/models), but
    unlike Ollama's (which lists every model pulled onto the server,
    installable/switchable at will) it will almost always report exactly
    one entry — whichever single model the server was launched with.

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
        temperature: float | None = None,
        thinking: bool | str = False,
        instance_name: str | None = None,
    ):
        if instance_name:
            self.name = instance_name
        self._base_url = base_url.rstrip("/")
        self._model = model
        self.model = model or "(server default)"
        self._temperature = temperature
        # llama.cpp server's own equivalent of Ollama's "think" field:
        # reasoning_effort in the request body — "none" disables reasoning
        # entirely, "low"/"medium"/"high" request graded effort on models
        # that support it (unsupported models just ignore the value).
        # False/True from the shared thinking config map onto "none"/
        # "medium" so the SAME per-instance setting works across both
        # local providers without the caller needing to know which API
        # shape is underneath. Same default-off reasoning as Ollama's: a
        # hidden reasoning pass can otherwise exhaust the generation
        # budget before the strict index/format output is ever written.
        self._reasoning_effort = _resolve_reasoning_effort(thinking)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout, headers=headers)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _chat_request(self, messages: list[dict]) -> httpx.Response:
        body = {"messages": messages, "reasoning_effort": self._reasoning_effort}
        if self._model:
            body["model"] = self._model
        if self._temperature is not None:
            body["temperature"] = self._temperature
        return await self._client.post("/v1/chat/completions", json=body)

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
        return await self._chat_with_retry([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": build_user_prompt(dialogue_text)},
        ])

    async def ask(self, prompt: str) -> str:
        """No system prompt — the caller's text is the entire message.
        Used by app.engine.language_check's batched language audit, not
        translate()'s subtitle-specific prompt scheme."""
        return await self._chat_with_retry([{"role": "user", "content": prompt}])

    async def _chat_with_retry(self, messages: list[dict]) -> str:
        for attempt in (1, 2):
            try:
                resp = await asyncio.wait_for(
                    self._chat_request(messages),
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
                raise ProviderRateLimitedError(
                    f"llama.cpp request timed out: {exc or type(exc).__name__}"
                ) from exc
            except httpx.ConnectError as exc:
                raise ProviderRateLimitedError(
                    f"llama.cpp connection failed: {exc or type(exc).__name__}"
                ) from exc
            except httpx.TransportError as exc:
                # Catch-all for other transport-level drops (ReadError,
                # WriteError, RemoteProtocolError, ...) not specific
                # enough to warrant their own message above — e.g. a
                # remote server or tunnel (Tailscale, reverse proxy)
                # resetting the connection mid-response on a long batch.
                # Confirmed live: httpx.ReadError partway through batch
                # 14/37 against a remote instance, previously uncaught
                # here. str(exc) is often EMPTY for these (httpcore raises
                # ReadError() with no message on a dropped connection) —
                # falls back to the exception's class name so the item's
                # error_message is never blank, which is what actually
                # happened here (translate_item's own error handling
                # correctly wrote error_message=str(exc), but str(exc)
                # itself was "").
                detail = str(exc) or type(exc).__name__
                raise ProviderRateLimitedError(f"llama.cpp connection dropped: {detail}") from exc

        if resp.status_code == 429:
            raise ProviderRateLimitedError("llama.cpp returned 429")
        if resp.status_code != 200:
            raise ProviderError(f"llama.cpp request failed ({resp.status_code}): {resp.text}")

        data = resp.json()
        try:
            message = data["choices"][0]["message"]
            content = message["content"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"Unexpected llama.cpp response shape: {data}") from exc
        if not content:
            reasoning = message.get("reasoning_content")
            if reasoning:
                logger.warning(
                    "llama.cpp response content was empty but reasoning_content was "
                    "populated (%d chars) — model likely exhausted its token budget "
                    "during reasoning before writing content.",
                    len(reasoning),
                )
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

    async def list_models(self) -> list[dict]:
        """Returns whatever /v1/models reports — for llama.cpp this is
        normally exactly one entry (the model the server was launched
        with via CLI flags), unlike Ollama's list of every locally-pulled
        model. Still useful for the Engines page's model field: lets the
        user pick the exact model id/name /v1/models reports instead of
        guessing it (see the `model` field's hint — some server builds/
        reverse proxies reject a request with no `model` field, or a
        wrong one, at all)."""
        resp = await self._client.get("/v1/models")
        resp.raise_for_status()
        return [{"name": m.get("id")} for m in resp.json().get("data", []) if m.get("id")]
