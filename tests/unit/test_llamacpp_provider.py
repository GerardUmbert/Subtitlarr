import asyncio
import json as _json

import httpx
import pytest
import respx

from app.providers.base import ProviderError, ProviderRateLimitedError
from app.providers.llamacpp_provider import LlamaCppProvider


@pytest.mark.asyncio
@respx.mock
async def test_llamacpp_translate_uses_real_system_prompt():
    route = respx.post("http://localhost:8080/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "1\nHola."}}]}
        )
    )
    provider = LlamaCppProvider(base_url="http://localhost:8080")
    await provider.translate("1\nHello.", "en", "es")

    sent_body = _json.loads(route.calls[0].request.content)
    system_content = sent_body["messages"][0]["content"]
    assert "professional subtitle translator" in system_content
    assert sent_body["messages"][1]["content"] == "1\nHello."
    # No `model` field — the server is started with one fixed model
    # already loaded, unlike the cloud OpenAI-compatible providers.
    assert "model" not in sent_body
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_llamacpp_translate_does_not_rechunk_or_modify_input():
    respx.post("http://localhost:8080/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "1\nHola.\n\n2\n¿Cómo estás?"}}]}
        )
    )
    provider = LlamaCppProvider(base_url="http://localhost:8080")
    dialogue_text = "1\nHello.\n\n2\nHow are you?"
    result = await provider.translate(dialogue_text, "en", "es")

    assert result == "1\nHola.\n\n2\n¿Cómo estás?"
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_llamacpp_translate_raises_on_non_200():
    respx.post("http://localhost:8080/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="server error")
    )
    provider = LlamaCppProvider(base_url="http://localhost:8080")
    with pytest.raises(ProviderError):
        await provider.translate("1\nHello.", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_llamacpp_translate_raises_on_unexpected_response_shape():
    respx.post("http://localhost:8080/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"})
    )
    provider = LlamaCppProvider(base_url="http://localhost:8080")
    with pytest.raises(ProviderError):
        await provider.translate("1\nHello.", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_llamacpp_translate_connection_error_is_retryable():
    respx.post("http://localhost:8080/v1/chat/completions").mock(
        side_effect=httpx.ConnectError("refused")
    )
    provider = LlamaCppProvider(base_url="http://localhost:8080")
    with pytest.raises(ProviderRateLimitedError):
        await provider.translate("1\nHello.", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_llamacpp_watchdog_retries_once_then_gives_up(monkeypatch):
    """Same watchdog pattern as Ollama: a request that hangs past
    WATCHDOG_TIMEOUT_SECONDS gets cancelled and retried once. If the
    retry ALSO hangs, give up with a clear error rather than looping."""
    import app.providers.llamacpp_provider as llamacpp_module

    async def always_hangs(*args, **kwargs):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(
        llamacpp_module.asyncio, "wait_for", lambda coro, timeout: always_hangs()
    )

    provider = LlamaCppProvider(base_url="http://localhost:8080")
    with pytest.raises(ProviderRateLimitedError) as excinfo:
        await provider.translate("1\nHello.", "en", "es")
    assert "still stuck" in str(excinfo.value)
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_llamacpp_test_connection_reports_ok_with_model():
    respx.get("http://localhost:8080/health").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    respx.get("http://localhost:8080/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "my-model.gguf"}]})
    )
    provider = LlamaCppProvider(base_url="http://localhost:8080")
    status = await provider.test_connection()
    assert status.ok is True
    assert "my-model.gguf" in status.detail
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_llamacpp_test_connection_reports_loading():
    respx.get("http://localhost:8080/health").mock(
        return_value=httpx.Response(503, json={"error": "loading model"})
    )
    provider = LlamaCppProvider(base_url="http://localhost:8080")
    status = await provider.test_connection()
    assert status.ok is False
    assert "loading" in status.detail.lower()
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_llamacpp_test_connection_reports_unreachable():
    respx.get("http://localhost:8080/health").mock(side_effect=httpx.ConnectError("refused"))
    provider = LlamaCppProvider(base_url="http://localhost:8080")
    status = await provider.test_connection()
    assert status.ok is False
    await provider.aclose()
