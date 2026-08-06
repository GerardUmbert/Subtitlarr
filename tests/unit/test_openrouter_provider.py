import json as _json

import httpx
import pytest
import respx

from app.providers.base import ProviderError, ProviderRateLimitedError
from app.providers.openrouter_provider import OpenRouterDailyLimitError, OpenRouterProvider


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_translate_uses_real_system_prompt():
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "1\nHola."}}]}
        )
    )
    provider = OpenRouterProvider(api_key="testkey")
    await provider.translate("1\nHello.", "en", "es")

    sent_body = _json.loads(route.calls[0].request.content)
    system_content = sent_body["messages"][0]["content"]
    assert "professional subtitle translator" in system_content
    assert sent_body["messages"][1]["content"] == "1\nHello."
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_translate_does_not_rechunk_or_modify_input():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "1\nHola.\n\n2\n¿Cómo estás?"}}]}
        )
    )
    provider = OpenRouterProvider(api_key="testkey")
    dialogue_text = "1\nHello.\n\n2\nHow are you?"
    result = await provider.translate(dialogue_text, "en", "es")

    assert result == "1\nHola.\n\n2\n¿Cómo estás?"
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_translate_rate_limited_raises_retryable():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(429)
    )
    provider = OpenRouterProvider(api_key="testkey")
    with pytest.raises(ProviderRateLimitedError):
        await provider.translate("1\nHello.", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_translate_raises_on_non_200_non_5xx():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(418, text="I'm a teapot")
    )
    provider = OpenRouterProvider(api_key="testkey")
    with pytest.raises(ProviderError):
        await provider.translate("1\nHello.", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("status_code", [500, 502, 503, 504, 529])
async def test_openrouter_translate_treats_5xx_as_retryable(status_code):
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(status_code, text="server trouble")
    )
    provider = OpenRouterProvider(api_key="testkey")
    with pytest.raises(ProviderRateLimitedError):
        await provider.translate("1\nHello.", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_translate_raises_on_unexpected_response_shape():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"})
    )
    provider = OpenRouterProvider(api_key="testkey")
    with pytest.raises(ProviderError):
        await provider.translate("1\nHello.", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_test_connection_reports_ok():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    )
    provider = OpenRouterProvider(api_key="testkey")
    status = await provider.test_connection()
    assert status.ok is True
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_test_connection_reports_error_detail():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(401, text="unauthorized")
    )
    provider = OpenRouterProvider(api_key="badkey")
    status = await provider.test_connection()
    assert status.ok is False
    assert "401" in status.detail
    await provider.aclose()


def test_openrouter_defaults_to_gemma_free_tier():
    provider = OpenRouterProvider(api_key="testkey")
    assert provider._model == "google/gemma-4-26b-a4b-it:free"


@pytest.mark.asyncio
@respx.mock
async def test_429_sets_retry_after_from_default_when_no_header():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(429)
    )
    provider = OpenRouterProvider(api_key="testkey")
    with pytest.raises(ProviderRateLimitedError) as excinfo:
        await provider.translate("1\nHello.", "en", "es")
    assert excinfo.value.retry_after_seconds == 62.0
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_429_sets_retry_after_from_response_header():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "12"})
    )
    provider = OpenRouterProvider(api_key="testkey")
    with pytest.raises(ProviderRateLimitedError) as excinfo:
        await provider.translate("1\nHello.", "en", "es")
    assert excinfo.value.retry_after_seconds == 12.0
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_5xx_does_not_set_retry_after():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(503)
    )
    provider = OpenRouterProvider(api_key="testkey")
    with pytest.raises(ProviderRateLimitedError) as excinfo:
        await provider.translate("1\nHello.", "en", "es")
    assert excinfo.value.retry_after_seconds is None
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_429_blocks_a_later_call_on_the_same_provider_instance(monkeypatch):
    import app.providers.openrouter_provider as openrouter_module

    calls = {"post": 0}
    route = respx.post("https://openrouter.ai/api/v1/chat/completions")

    def responder(request):
        calls["post"] += 1
        if calls["post"] == 1:
            return httpx.Response(429, headers={"Retry-After": "5"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    route.mock(side_effect=responder)

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(openrouter_module.asyncio, "sleep", fake_sleep)

    provider = OpenRouterProvider(api_key="testkey")

    with pytest.raises(ProviderRateLimitedError):
        await provider.translate("1\nHello.", "en", "es")

    await provider.translate("2\nHello again.", "en", "es")

    assert slept == pytest.approx([5.0], abs=0.01)
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_daily_limit_429_raises_daily_limit_error_not_retryable():
    """OpenRouter's free tier caps at 50 requests/day (confirmed via
    https://openrouter.ai/docs/api_reference/limits) — this is distinct
    from the 20 RPM per-minute throttle: retrying within the same run
    can't fix it, so it must NOT be a ProviderRateLimitedError (which the
    runner retries), it must hard-fail the item instead."""
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            429,
            json={
                "error": {
                    "code": 429,
                    "message": "Daily rate limit exceeded",
                    "metadata": {"error_type": "rate_limit_exceeded_daily"},
                }
            },
        )
    )
    provider = OpenRouterProvider(api_key="testkey")
    with pytest.raises(OpenRouterDailyLimitError):
        await provider.translate("1\nHello.", "en", "es")
    # Must NOT also be catchable as a retryable rate-limit error.
    assert not isinstance(OpenRouterDailyLimitError("x"), ProviderRateLimitedError)
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_per_minute_429_without_daily_marker_stays_retryable():
    """A plain per-minute 429 (no daily marker in the error body) must
    still take the normal retryable path, not be misclassified as a daily
    cap."""
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            429,
            json={
                "error": {
                    "code": 429,
                    "message": "Rate limit exceeded",
                    "metadata": {"error_type": "rate_limit_exceeded"},
                }
            },
        )
    )
    provider = OpenRouterProvider(api_key="testkey")
    with pytest.raises(ProviderRateLimitedError):
        await provider.translate("1\nHello.", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_429_uses_x_ratelimit_reset_header_when_no_retry_after():
    """OpenRouter's documented convention: X-RateLimit-Reset is
    milliseconds-since-epoch, used when Retry-After isn't present."""
    import time as _time

    reset_at_ms = (_time.time() + 30) * 1000
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            429, headers={"X-RateLimit-Reset": str(int(reset_at_ms))}
        )
    )
    provider = OpenRouterProvider(api_key="testkey")
    with pytest.raises(ProviderRateLimitedError) as excinfo:
        await provider.translate("1\nHello.", "en", "es")
    assert excinfo.value.retry_after_seconds == pytest.approx(30.0, abs=1.0)
    await provider.aclose()
