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
async def test_nvidia_translate_raises_on_non_200_non_5xx():
    respx.post("https://integrate.api.nvidia.com/v1/chat/completions").mock(
        return_value=httpx.Response(418, text="I'm a teapot")
    )
    provider = NvidiaProvider(api_key="testkey")
    with pytest.raises(ProviderError):
        await provider.translate("1\nHello.", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("status_code", [500, 502, 503, 504, 529])
async def test_nvidia_translate_treats_5xx_as_retryable(status_code):
    """Confirmed live: 504 Gateway Timeout and 529 Site Overloaded both hit
    while NVIDIA's own backend was struggling, not from anything wrong
    with the request — these must be retryable (same as 429), not hard
    failures, so a transient outage doesn't kill the whole item."""
    respx.post("https://integrate.api.nvidia.com/v1/chat/completions").mock(
        return_value=httpx.Response(status_code, text="server trouble")
    )
    provider = NvidiaProvider(api_key="testkey")
    with pytest.raises(ProviderRateLimitedError):
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


@pytest.mark.asyncio
@respx.mock
async def test_429_sets_retry_after_from_default_when_no_header():
    respx.post("https://integrate.api.nvidia.com/v1/chat/completions").mock(
        return_value=httpx.Response(429)
    )
    provider = NvidiaProvider(api_key="testkey")
    with pytest.raises(ProviderRateLimitedError) as excinfo:
        await provider.translate("1\nHello.", "en", "es")
    assert excinfo.value.retry_after_seconds == 62.0
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_429_sets_retry_after_from_response_header():
    respx.post("https://integrate.api.nvidia.com/v1/chat/completions").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "12"})
    )
    provider = NvidiaProvider(api_key="testkey")
    with pytest.raises(ProviderRateLimitedError) as excinfo:
        await provider.translate("1\nHello.", "en", "es")
    assert excinfo.value.retry_after_seconds == 12.0
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_5xx_does_not_set_retry_after(monkeypatch):
    """Only a real 429 sets retry_after_seconds — a transient 5xx has no
    shared gate to rely on, so the caller (translator._translate_batch)
    must fall back to its own pause_between_items_seconds."""
    respx.post("https://integrate.api.nvidia.com/v1/chat/completions").mock(
        return_value=httpx.Response(503)
    )
    provider = NvidiaProvider(api_key="testkey")
    with pytest.raises(ProviderRateLimitedError) as excinfo:
        await provider.translate("1\nHello.", "en", "es")
    assert excinfo.value.retry_after_seconds is None
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_429_blocks_a_later_call_on_the_same_provider_instance(monkeypatch):
    """The core of the run-wide cooldown: a 429 on one call must make a
    LATER call on the same provider instance wait, without that later
    call needing to hit its own 429 first — this is what lets every other
    batch/item in the run avoid piling more requests onto an exhausted
    rate limit window."""
    import app.providers.nvidia_provider as nvidia_module

    calls = {"post": 0}
    route = respx.post("https://integrate.api.nvidia.com/v1/chat/completions")

    def responder(request):
        calls["post"] += 1
        if calls["post"] == 1:
            return httpx.Response(429, headers={"Retry-After": "5"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    route.mock(side_effect=responder)

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(nvidia_module.asyncio, "sleep", fake_sleep)

    provider = NvidiaProvider(api_key="testkey")

    with pytest.raises(ProviderRateLimitedError):
        await provider.translate("1\nHello.", "en", "es")

    # A second, independent call on the SAME provider instance must wait
    # for the gate before even sending its request.
    await provider.translate("2\nHello again.", "en", "es")

    assert slept == pytest.approx([5.0], abs=0.01)  # the gate's own wait, triggered by the 2nd call
    await provider.aclose()
