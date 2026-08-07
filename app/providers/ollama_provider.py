import asyncio
import json
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


# A flat, generous-but-bounded request timeout. A real live request timed
# out at a fixed 300s once num_ctx (and therefore batch size) was raised —
# actual translation time on this hardware runs ~3 minutes even for large
# batches, so 600s gives real margin (~3x normal) without ballooning into
# a many-minutes ceiling that would mask a genuinely stuck request.
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 600.0

# Watchdog: if a single translate request runs longer than this with no
# response, something is likely wedged (observed live: GPU compute/VRAM not
# maxed during a 5+ minute "stuck-looking" request). Rather than just wait
# out the full 600s httpx timeout, cancel at this shorter threshold, force
# the model out of Ollama's memory (keep_alive=0 — there is no HTTP endpoint
# to abort an in-progress generation directly), and retry exactly once. If
# the retry also exceeds the threshold, give up — surface a real failure
# rather than looping forever.
WATCHDOG_TIMEOUT_SECONDS = 300.0


def _default_timeout_for_context(num_ctx: int) -> float:
    return DEFAULT_OLLAMA_TIMEOUT_SECONDS


class OllamaProvider(TranslationProvider):
    name = "ollama"
    provider_type = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float | None = None,
        num_ctx: int = 8192,
        instance_name: str | None = None,
    ):
        if instance_name:
            self.name = instance_name
        self._base_url = base_url.rstrip("/")
        self._model = model
        self.model = model
        # Ollama defaults to a conservative 4096-token context regardless of
        # what the model actually supports (Gemma 3 supports up to 128K) —
        # without this, a batch of subtitle cues larger than 4096 tokens
        # gets silently truncated server-side before translation even
        # starts, which is what caused a real 0/1071-cues-recovered failure.
        self._num_ctx = num_ctx
        resolved_timeout = timeout if timeout is not None else _default_timeout_for_context(num_ctx)
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=resolved_timeout)
        # Pulling a multi-GB model can take many minutes — use a client with
        # no read timeout dedicated to that one streaming request.
        self._pull_client = httpx.AsyncClient(base_url=self._base_url, timeout=None)

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._pull_client.aclose()

    async def pull_model(self, model: str | None = None):
        """Streams Ollama's /api/pull progress events. Yields dicts like
        {"status": "downloading", "completed": N, "total": M, "digest": "..."}
        or {"status": "success"} on completion. Caller (see api/engines.py)
        is responsible for tracking/publishing this progress since a pull
        can take many minutes — this method just yields as data arrives."""
        target_model = model or self._model
        async with self._pull_client.stream(
            "POST", "/api/pull", json={"model": target_model, "stream": True}
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise ProviderError(f"Ollama pull failed ({resp.status_code}): {body}")
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if "error" in event:
                    raise ProviderError(f"Ollama pull error: {event['error']}")
                yield event

    async def _chat_request(self, system_prompt: str, dialogue_text: str) -> httpx.Response:
        return await self._client.post(
            "/api/chat",
            json={
                "model": self._model,
                "stream": False,
                "options": {"num_ctx": self._num_ctx},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": build_user_prompt(dialogue_text)},
                ],
            },
        )

    async def _force_unload_model(self) -> None:
        """Evicts the model from Ollama's memory immediately (keep_alive=0),
        used to break a wedged request out of whatever state it's stuck in.
        Best-effort — if the unload call itself fails, the retry proceeds
        anyway rather than compounding one failure into two."""
        try:
            await self._client.post(
                "/api/chat",
                json={"model": self._model, "messages": [], "keep_alive": 0},
            )
        except httpx.HTTPError as exc:
            logger.warning("Ollama force-unload after watchdog timeout failed: %s", exc)

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

        # Reload-then-retry (force-unload the model, exactly once) applies
        # to failures that indicate the server RESPONDED but got stuck or
        # errored — a watchdog timeout, an httpx-level timeout, or a 5xx —
        # since those are the failure modes a wedged/crashed model process
        # can actually recover from. Deliberately NOT attempted for
        # httpx.ConnectError: if Ollama's process isn't even reachable,
        # there's no loaded model state to clear, so a reload call there is
        # just wasted time before the same connection failure repeats.
        for attempt in (1, 2):
            try:
                resp = await asyncio.wait_for(
                    self._chat_request(system_prompt, dialogue_text),
                    timeout=WATCHDOG_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                if attempt == 2:
                    raise ProviderRateLimitedError(
                        f"Ollama request still stuck after {WATCHDOG_TIMEOUT_SECONDS:.0f}s "
                        "on retry — giving up."
                    )
                logger.warning(
                    "Ollama request exceeded watchdog timeout (%.0fs) with no response; "
                    "force-unloading model and retrying once.",
                    WATCHDOG_TIMEOUT_SECONDS,
                )
                await self._force_unload_model()
                continue
            except httpx.TimeoutException as exc:
                if attempt == 2:
                    raise ProviderRateLimitedError(f"Ollama request timed out: {exc}") from exc
                logger.warning(
                    "Ollama request timed out (%s); force-unloading model and retrying once.", exc
                )
                await self._force_unload_model()
                continue
            except httpx.ConnectError as exc:
                raise ProviderRateLimitedError(f"Ollama connection failed: {exc}") from exc

            if resp.status_code >= 500:
                if attempt == 2:
                    raise ProviderRateLimitedError(
                        f"Ollama returned {resp.status_code} again after reload+retry: {resp.text}"
                    )
                logger.warning(
                    "Ollama returned %d (%s); force-unloading model and retrying once.",
                    resp.status_code, resp.text,
                )
                await self._force_unload_model()
                continue
            break

        if resp.status_code == 429:
            raise ProviderRateLimitedError("Ollama returned 429")
        if resp.status_code != 200:
            raise ProviderError(f"Ollama request failed ({resp.status_code}): {resp.text}")

        data = resp.json()
        content = data.get("message", {}).get("content")
        if not content:
            raise ProviderError(f"Ollama response missing message content: {data}")
        return content

    async def test_connection(self) -> ProviderStatus:
        try:
            resp = await self._client.get("/api/tags")
        except httpx.HTTPError as exc:
            return ProviderStatus(ok=False, detail=str(exc))
        if resp.status_code != 200:
            return ProviderStatus(ok=False, detail=f"HTTP {resp.status_code}")
        models = [m.get("name") for m in resp.json().get("models", [])]
        if self._model not in models:
            return ProviderStatus(
                ok=False, detail=f"Model '{self._model}' not found. Available: {models}"
            )
        return ProviderStatus(ok=True, detail=f"responded, model '{self._model}' available")

    async def list_models(self) -> list[dict]:
        """Returns every model already pulled on this Ollama server — lets
        the Engine page offer a dropdown of what's actually available
        instead of a bare free-text field, distinct from pulling a NEW
        model by name."""
        resp = await self._client.get("/api/tags")
        resp.raise_for_status()
        return [
            {
                "name": m.get("name"),
                "parameter_size": m.get("details", {}).get("parameter_size"),
                "quantization": m.get("details", {}).get("quantization_level"),
                "size_bytes": m.get("size"),
            }
            for m in resp.json().get("models", [])
        ]
