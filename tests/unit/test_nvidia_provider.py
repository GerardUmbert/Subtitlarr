import json as _json

import httpx
import pytest
import respx

from app.providers.base import ProviderError, ProviderRateLimitedError
from app.providers.nvidia_provider import NvidiaProvider


@pytest.mark.asyncio
@respx.mock
async def test_nvidia_translate_uses_real_system_prompt():
    """Unlike the dedicated Riva Translate model this provider replaced,
    NVIDIA's chat models (default: DeepSeek V4 Flash) ARE instructable —
    this provider reuses the same system/user prompt scheme as
    Ollama/Gemini, not a bare language-pair code."""
    route = respx.post("https://integrate.api.nvidia.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "1\nHola."}}]}
        )
    )
    provider = NvidiaProvider(api_key="testkey")
    await provider.translate("1\nHello.", "en", "es")

    sent_body = _json.loads(route.calls[0].request.content)
    system_content = sent_body["messages"][0]["content"]
    assert "professional subtitle translator" in system_content
    assert sent_body["messages"][1]["content"] == "1\nHello."
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_nvidia_translate_does_not_rechunk_or_modify_input():
    """No internal re-chunking or marker-joining — unlike the old Riva
    provider, this one sends the dialogue_text through unmodified and
    returns the response as-is; batching is the caller's (chunk_cues())
    responsibility, same as Ollama/Gemini."""
    respx.post("https://integrate.api.nvidia.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "1\nHola.\n\n2\n¿Cómo estás?"}}]}
        )
    )
    provider = NvidiaProvider(api_key="testkey")
    dialogue_text = "1\nHello.\n\n2\nHow are you?"
    result = await provider.translate(dialogue_text, "en", "es")

    assert result == "1\nHola.\n\n2\n¿Cómo estás?"
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_nvidia_translate_rate_limited_raises_retryable():
    respx.post("https://integrate.api.nvidia.com/v1/chat/completions").mock(
        return_value=httpx.Response(429)
    )
    provider = NvidiaProvider(api_key="testkey")
    with pytest.raises(ProviderRateLimitedError):
        await provider.translate("1\nHello.", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_nvidia_translate_raises_on_non_200():
    respx.post("https://integrate.api.nvidia.com/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="server error")
    )
    provider = NvidiaProvider(api_key="testkey")
    with pytest.raises(ProviderError):
        await provider.translate("1\nHello.", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_nvidia_translate_raises_on_unexpected_response_shape():
    respx.post("https://integrate.api.nvidia.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"})
    )
    provider = NvidiaProvider(api_key="testkey")
    with pytest.raises(ProviderError):
        await provider.translate("1\nHello.", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_nvidia_test_connection_reports_ok():
    respx.post("https://integrate.api.nvidia.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    )
    provider = NvidiaProvider(api_key="testkey")
    status = await provider.test_connection()
    assert status.ok is True
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_nvidia_test_connection_reports_error_detail():
    respx.post("https://integrate.api.nvidia.com/v1/chat/completions").mock(
        return_value=httpx.Response(401, text="unauthorized")
    )
    provider = NvidiaProvider(api_key="badkey")
    status = await provider.test_connection()
    assert status.ok is False
    assert "401" in status.detail
    await provider.aclose()


def test_nvidia_defaults_to_deepseek_v4_flash():
    provider = NvidiaProvider(api_key="testkey")
    assert provider._model == "deepseek-ai/deepseek-v4-flash"
