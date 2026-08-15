import json as _json

import httpx
import pytest
import respx

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import ProviderContentBlockedError, ProviderError, ProviderRateLimitedError


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_translate_uses_real_system_prompt():
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, json={"content": [{"type": "text", "text": "1\nHola."}]}
        )
    )
    provider = AnthropicProvider(api_key="testkey", model="claude-haiku-4-5-20251001")
    await provider.translate("1\nHello.", "en", "es")

    sent_body = _json.loads(route.calls[0].request.content)
    assert "professional subtitle translator" in sent_body["system"]
    assert sent_body["messages"][0]["content"] == "1\nHello."
    assert sent_body["messages"][0]["role"] == "user"
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_translate_does_not_rechunk_or_modify_input():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200, json={"content": [{"type": "text", "text": "1\nHola.\n\n2\n¿Cómo estás?"}]}
        )
    )
    provider = AnthropicProvider(api_key="testkey", model="claude-haiku-4-5-20251001")
    dialogue_text = "1\nHello.\n\n2\nHow are you?"
    result = await provider.translate(dialogue_text, "en", "es")

    assert result == "1\nHola.\n\n2\n¿Cómo estás?"
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_sends_auth_headers():
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})
    )
    provider = AnthropicProvider(api_key="testkey", model="claude-haiku-4-5-20251001")
    await provider.translate("1\nHello.", "en", "es")

    sent_headers = route.calls[0].request.headers
    assert sent_headers["x-api-key"] == "testkey"
    assert sent_headers["anthropic-version"] == "2023-06-01"
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_ask_sends_no_system_field():
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})
    )
    provider = AnthropicProvider(api_key="testkey", model="claude-haiku-4-5-20251001")
    await provider.ask("What language is this?")

    sent_body = _json.loads(route.calls[0].request.content)
    assert "system" not in sent_body
    assert sent_body["messages"][0]["content"] == "What language is this?"
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_translate_rate_limited_raises_retryable():
    respx.post("https://api.anthropic.com/v1/messages").mock(return_value=httpx.Response(429))
    provider = AnthropicProvider(api_key="testkey", model="claude-haiku-4-5-20251001")
    with pytest.raises(ProviderRateLimitedError):
        await provider.translate("1\nHello.", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_translate_raises_on_non_200_non_5xx():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(418, text="I'm a teapot")
    )
    provider = AnthropicProvider(api_key="testkey", model="claude-haiku-4-5-20251001")
    with pytest.raises(ProviderError):
        await provider.translate("1\nHello.", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("status_code", [500, 502, 503, 504, 529])
async def test_anthropic_translate_treats_5xx_and_529_as_retryable(status_code):
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(status_code, text="server trouble")
    )
    provider = AnthropicProvider(api_key="testkey", model="claude-haiku-4-5-20251001")
    with pytest.raises(ProviderRateLimitedError):
        await provider.translate("1\nHello.", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_translate_raises_on_unexpected_response_shape():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"})
    )
    provider = AnthropicProvider(api_key="testkey", model="claude-haiku-4-5-20251001")
    with pytest.raises(ProviderError):
        await provider.translate("1\nHello.", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_translate_raises_content_blocked_on_refusal():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": ""}],
                "stop_reason": "refusal",
            },
        )
    )
    provider = AnthropicProvider(api_key="testkey", model="claude-haiku-4-5-20251001")
    with pytest.raises(ProviderContentBlockedError):
        await provider.translate("1\nHello.", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_test_connection_reports_ok():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})
    )
    provider = AnthropicProvider(api_key="testkey", model="claude-haiku-4-5-20251001")
    status = await provider.test_connection()
    assert status.ok is True
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_test_connection_reports_error_detail():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(401, text="unauthorized")
    )
    provider = AnthropicProvider(api_key="badkey", model="claude-haiku-4-5-20251001")
    status = await provider.test_connection()
    assert status.ok is False
    assert "401" in status.detail
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_429_sets_retry_after_from_default_when_no_header():
    respx.post("https://api.anthropic.com/v1/messages").mock(return_value=httpx.Response(429))
    provider = AnthropicProvider(api_key="testkey", model="claude-haiku-4-5-20251001")
    with pytest.raises(ProviderRateLimitedError) as excinfo:
        await provider.translate("1\nHello.", "en", "es")
    assert excinfo.value.retry_after_seconds == 62.0
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_429_sets_retry_after_from_response_header():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(429, headers={"retry-after": "12"})
    )
    provider = AnthropicProvider(api_key="testkey", model="claude-haiku-4-5-20251001")
    with pytest.raises(ProviderRateLimitedError) as excinfo:
        await provider.translate("1\nHello.", "en", "es")
    assert excinfo.value.retry_after_seconds == 12.0
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_429_blocks_a_later_call_on_the_same_provider_instance(monkeypatch):
    import app.providers.anthropic_provider as anthropic_module

    calls = {"post": 0}
    route = respx.post("https://api.anthropic.com/v1/messages")

    def responder(request):
        calls["post"] += 1
        if calls["post"] == 1:
            return httpx.Response(429, headers={"retry-after": "5"})
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    route.mock(side_effect=responder)

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(anthropic_module.asyncio, "sleep", fake_sleep)

    provider = AnthropicProvider(api_key="testkey", model="claude-haiku-4-5-20251001")

    with pytest.raises(ProviderRateLimitedError):
        await provider.translate("1\nHello.", "en", "es")

    await provider.translate("2\nHello again.", "en", "es")

    assert slept == pytest.approx([5.0], abs=0.01)
    await provider.aclose()
