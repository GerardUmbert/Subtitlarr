import httpx
import pytest
import respx

from app.config import Settings
from app.providers.base import ProviderError, ProviderRateLimitedError
from app.providers.gemini_provider import GeminiProvider
from app.providers.ollama_provider import OllamaProvider
from app.providers.registry import get_active_provider, get_fallback_provider


@pytest.mark.asyncio
@respx.mock
async def test_ollama_translate_success():
    respx.post("http://ollama.test:11434/api/chat").mock(
        return_value=httpx.Response(
            200, json={"message": {"content": "1\nHola.\n\n2\n¿Cómo estás?"}}
        )
    )
    provider = OllamaProvider(base_url="http://ollama.test:11434", model="gemma3:4b")
    result = await provider.translate("1\nHello\n\n2\nHow are you?", "en", "es")
    assert "Hola." in result
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_ollama_translate_sends_num_ctx():
    """Regression test: Ollama defaults to a 4096-token context regardless
    of what the model supports, silently truncating long subtitle files
    before translation even starts. The request must explicitly set
    options.num_ctx rather than relying on Ollama's conservative default."""
    route = respx.post("http://ollama.test:11434/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": "1\nHola."}})
    )
    provider = OllamaProvider(base_url="http://ollama.test:11434", model="gemma3:4b", num_ctx=16384)
    await provider.translate("1\nHello", "en", "es")
    sent_body = route.calls[0].request.content
    import json as _json

    payload = _json.loads(sent_body)
    assert payload["options"]["num_ctx"] == 16384
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_ollama_translate_rate_limited_raises_retryable():
    respx.post("http://ollama.test:11434/api/chat").mock(return_value=httpx.Response(429))
    provider = OllamaProvider(base_url="http://ollama.test:11434", model="gemma3:4b")
    with pytest.raises(ProviderRateLimitedError):
        await provider.translate("1\nHello", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_ollama_test_connection_model_missing():
    respx.get("http://ollama.test:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})
    )
    provider = OllamaProvider(base_url="http://ollama.test:11434", model="gemma3:4b")
    status = await provider.test_connection()
    assert status.ok is False
    assert "gemma3:4b" in status.detail
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_gemini_translate_success():
    respx.post("https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent").mock(
        return_value=httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "1\nHola."}]}}]},
        )
    )
    provider = GeminiProvider(api_key="testkey", model="gemini-1.5-flash")
    result = await provider.translate("1\nHello", "en", "es")
    assert result == "1\nHola."
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_gemini_rate_limited_raises_retryable():
    respx.post("https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent").mock(
        return_value=httpx.Response(429)
    )
    provider = GeminiProvider(api_key="testkey", model="gemini-1.5-flash")
    with pytest.raises(ProviderRateLimitedError):
        await provider.translate("1\nHello", "en", "es")
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_gemini_429_blocks_a_later_call_on_the_same_provider_instance(monkeypatch):
    """Gemini now shares the same cooldown-gate pattern as NVIDIA/
    OpenRouter/Groq: a 429 on one call must make a LATER call on the same
    provider instance wait, without needing to hit its own 429 first."""
    import app.providers.gemini_provider as gemini_module

    calls = {"post": 0}
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
    )

    def responder(request):
        calls["post"] += 1
        if calls["post"] == 1:
            return httpx.Response(429, headers={"Retry-After": "5"})
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        )

    route.mock(side_effect=responder)

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(gemini_module.asyncio, "sleep", fake_sleep)

    provider = GeminiProvider(api_key="testkey", model="gemini-1.5-flash")

    with pytest.raises(ProviderRateLimitedError):
        await provider.translate("1\nHello.", "en", "es")

    await provider.translate("2\nHello again.", "en", "es")

    assert slept == pytest.approx([5.0], abs=0.01)
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_gemini_prompt_level_safety_block_raises_clear_error():
    """Regression test: a Gemini response blocked BEFORE generation
    (promptFeedback.blockReason set, no candidates at all) previously
    surfaced as a bare 'KeyError: candidates' with no useful detail —
    confirmed live on a real run (3/26 items failed this way, with the
    real reason being PROHIBITED_CONTENT, not SAFETY — the earlier fix
    only checked for SAFETY and missed this). The short message
    (str(exc)) must stay human-readable and name what happened; the full
    raw response goes on raw_detail instead of being crammed into the
    short message."""
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
    ).mock(
        return_value=httpx.Response(
            200, json={"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}
        )
    )
    provider = GeminiProvider(api_key="testkey", model="gemini-1.5-flash")
    with pytest.raises(ProviderError) as excinfo:
        await provider.translate("1\nHello.", "en", "es")
    assert "blocked" in str(excinfo.value).lower()
    assert "prohibited content" in str(excinfo.value).lower()
    assert "PROHIBITED_CONTENT" in excinfo.value.raw_detail
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_gemini_response_level_safety_block_raises_clear_error():
    """Same regression as above, for the OTHER blocked-response shape:
    generation started (a candidate exists) but its finishReason means
    the output was withheld and there's no content.parts — confirmed
    live as 'KeyError: parts' with no detail on a real run. Also checks
    finishMessage (Gemini's own human-readable explanation, when present)
    gets folded into the short message."""
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "finishReason": "PROHIBITED_CONTENT",
                        "finishMessage": "This output contains sensitive words.",
                    }
                ]
            },
        )
    )
    provider = GeminiProvider(api_key="testkey", model="gemini-1.5-flash")
    with pytest.raises(ProviderError) as excinfo:
        await provider.translate("1\nHello.", "en", "es")
    assert "blocked" in str(excinfo.value).lower()
    assert "prohibited content" in str(excinfo.value).lower()
    assert "sensitive words" in str(excinfo.value)
    assert "PROHIBITED_CONTENT" in excinfo.value.raw_detail
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_gemini_unmapped_response_shape_still_raises_with_raw_detail():
    """A response shape that matches none of the known block/finish
    reasons must still raise cleanly (not crash with an uncaught
    KeyError) and still carry the raw response for diagnosis."""
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
    ).mock(return_value=httpx.Response(200, json={"totally": "unexpected"}))
    provider = GeminiProvider(api_key="testkey", model="gemini-1.5-flash")
    with pytest.raises(ProviderError) as excinfo:
        await provider.translate("1\nHello.", "en", "es")
    assert excinfo.value.raw_detail is not None
    assert "totally" in excinfo.value.raw_detail
    await provider.aclose()


def test_registry_builds_active_and_fallback():
    settings = Settings(
        active_engine="ollama",
        fallback_engine="gemini",
        ollama_base_url="http://ollama.test:11434",
        ollama_model="gemma3:4b",
        gemini_api_key="testkey",
        gemini_model="gemini-1.5-flash",
    )
    active = get_active_provider(settings)
    fallback = get_fallback_provider(settings)
    assert active.name == "ollama"
    assert fallback.name == "gemini"


def test_registry_no_fallback_when_same_as_active():
    settings = Settings(active_engine="ollama", fallback_engine="ollama")
    assert get_fallback_provider(settings) is None


def test_registry_no_fallback_when_unset():
    settings = Settings(active_engine="ollama", fallback_engine="")
    assert get_fallback_provider(settings) is None


def test_registry_builds_nvidia():
    settings = Settings(active_engine="nvidia", nvidia_api_key="testkey")
    active = get_active_provider(settings)
    assert active.name == "nvidia"


def test_registry_builds_openrouter():
    settings = Settings(active_engine="openrouter", openrouter_api_key="testkey")
    active = get_active_provider(settings)
    assert active.name == "openrouter"


def test_registry_builds_groq():
    settings = Settings(active_engine="groq", groq_api_key="testkey")
    active = get_active_provider(settings)
    assert active.name == "groq"


def test_registry_builds_llamacpp():
    settings = Settings(active_engine="llamacpp")
    active = get_active_provider(settings)
    assert active.name == "llamacpp"
