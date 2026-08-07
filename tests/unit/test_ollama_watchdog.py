import asyncio
import json as _json

import httpx
import pytest
import respx

from app.providers.base import ProviderRateLimitedError
from app.providers.ollama_provider import OllamaProvider


@pytest.mark.asyncio
@respx.mock
async def test_watchdog_unloads_and_retries_once_then_succeeds(monkeypatch):
    """Regression test for a real live failure: a translation appeared
    stuck (GPU compute/VRAM not maxed) for 5+ minutes. Rather than just
    waiting out the full request timeout, the watchdog should cancel at a
    shorter threshold, force-unload the model, and retry once — succeeding
    if the retry responds normally."""
    import app.providers.ollama_provider as mod

    monkeypatch.setattr(mod, "WATCHDOG_TIMEOUT_SECONDS", 0.05)

    call_count = {"chat": 0, "unload": 0}

    async def side_effect(request):
        body = _json.loads(request.content)
        if body.get("keep_alive") == 0:
            call_count["unload"] += 1
            return httpx.Response(200, json={})
        call_count["chat"] += 1
        if call_count["chat"] == 1:
            # first real translate attempt: hang well past the watchdog threshold
            await asyncio.sleep(1)
            return httpx.Response(200, json={"message": {"content": "should not be reached"}})
        return httpx.Response(200, json={"message": {"content": "1\nHola."}})

    respx.post("http://ollama.test:11434/api/chat").mock(side_effect=side_effect)

    provider = OllamaProvider(base_url="http://ollama.test:11434", model="gemma3:4b")
    result = await provider.translate("1\nHello", "en", "es")

    assert result == "1\nHola."
    assert call_count["unload"] == 1
    assert call_count["chat"] == 2  # first hung, second succeeded
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_watchdog_gives_up_after_second_timeout(monkeypatch):
    """If the retry ALSO exceeds the watchdog threshold, stop — surface a
    real failure rather than looping forever."""
    import app.providers.ollama_provider as mod

    monkeypatch.setattr(mod, "WATCHDOG_TIMEOUT_SECONDS", 0.05)

    async def side_effect(request):
        body = _json.loads(request.content)
        if body.get("keep_alive") == 0:
            return httpx.Response(200, json={})
        await asyncio.sleep(1)  # every real translate attempt hangs
        return httpx.Response(200, json={"message": {"content": "unreachable"}})

    respx.post("http://ollama.test:11434/api/chat").mock(side_effect=side_effect)

    provider = OllamaProvider(base_url="http://ollama.test:11434", model="gemma3:4b")
    with pytest.raises(ProviderRateLimitedError, match="still stuck"):
        await provider.translate("1\nHello", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_5xx_triggers_reload_and_retries_once_then_succeeds():
    """A 5xx from Ollama itself (not a timeout) is also reload-recoverable
    — a wedged/crashed model process can produce a real error response,
    not just hang. Widened from the original watchdog-only behavior."""
    call_count = {"chat": 0, "unload": 0}

    async def side_effect(request):
        body = _json.loads(request.content)
        if body.get("keep_alive") == 0:
            call_count["unload"] += 1
            return httpx.Response(200, json={})
        call_count["chat"] += 1
        if call_count["chat"] == 1:
            return httpx.Response(500, text="internal error")
        return httpx.Response(200, json={"message": {"content": "1\nHola."}})

    respx.post("http://ollama.test:11434/api/chat").mock(side_effect=side_effect)

    provider = OllamaProvider(base_url="http://ollama.test:11434", model="gemma3:4b")
    result = await provider.translate("1\nHello", "en", "es")

    assert result == "1\nHola."
    assert call_count["unload"] == 1
    assert call_count["chat"] == 2
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_5xx_gives_up_after_second_failure():
    async def side_effect(request):
        body = _json.loads(request.content)
        if body.get("keep_alive") == 0:
            return httpx.Response(200, json={})
        return httpx.Response(503, text="still down")

    respx.post("http://ollama.test:11434/api/chat").mock(side_effect=side_effect)

    provider = OllamaProvider(base_url="http://ollama.test:11434", model="gemma3:4b")
    with pytest.raises(ProviderRateLimitedError, match="503"):
        await provider.translate("1\nHello", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_connect_error_does_not_attempt_reload():
    """If Ollama's process isn't even reachable, there's no loaded model
    state to clear — a reload attempt there is wasted time before the
    same connection failure repeats. Confirms NO unload call is made and
    the failure surfaces immediately, without a retry."""
    call_count = {"chat": 0, "unload": 0}

    async def side_effect(request):
        body = _json.loads(request.content)
        if body.get("keep_alive") == 0:
            call_count["unload"] += 1
            return httpx.Response(200, json={})
        call_count["chat"] += 1
        raise httpx.ConnectError("Connection refused", request=request)

    respx.post("http://ollama.test:11434/api/chat").mock(side_effect=side_effect)

    provider = OllamaProvider(base_url="http://ollama.test:11434", model="gemma3:4b")
    with pytest.raises(ProviderRateLimitedError, match="connection failed"):
        await provider.translate("1\nHello", "en", "es")

    assert call_count["unload"] == 0
    assert call_count["chat"] == 1  # no retry either — fails immediately
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_httpx_timeout_triggers_reload_and_retries_once_then_succeeds():
    """A lower-level httpx timeout (distinct from the watchdog's
    asyncio.wait_for) is also reload-recoverable — same reasoning as the
    watchdog case: the server may be stuck, not unreachable."""
    call_count = {"chat": 0, "unload": 0}

    async def side_effect(request):
        body = _json.loads(request.content)
        if body.get("keep_alive") == 0:
            call_count["unload"] += 1
            return httpx.Response(200, json={})
        call_count["chat"] += 1
        if call_count["chat"] == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json={"message": {"content": "1\nHola."}})

    respx.post("http://ollama.test:11434/api/chat").mock(side_effect=side_effect)

    provider = OllamaProvider(base_url="http://ollama.test:11434", model="gemma3:4b")
    result = await provider.translate("1\nHello", "en", "es")

    assert result == "1\nHola."
    assert call_count["unload"] == 1
    assert call_count["chat"] == 2
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_watchdog_does_not_interfere_with_a_normal_fast_response(monkeypatch):
    """The watchdog must not affect the common case — a request that
    responds well within the threshold succeeds on the first attempt, no
    unload call made."""
    import app.providers.ollama_provider as mod

    monkeypatch.setattr(mod, "WATCHDOG_TIMEOUT_SECONDS", 5.0)

    unload_calls = {"count": 0}

    async def side_effect(request):
        body = _json.loads(request.content)
        if body.get("keep_alive") == 0:
            unload_calls["count"] += 1
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"message": {"content": "1\nHola."}})

    respx.post("http://ollama.test:11434/api/chat").mock(side_effect=side_effect)

    provider = OllamaProvider(base_url="http://ollama.test:11434", model="gemma3:4b")
    result = await provider.translate("1\nHello", "en", "es")

    assert result == "1\nHola."
    assert unload_calls["count"] == 0
    await provider.aclose()
