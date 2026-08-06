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
