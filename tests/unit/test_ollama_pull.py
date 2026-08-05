import httpx
import pytest
import respx

from app.providers.ollama_provider import OllamaProvider
from app.providers.base import ProviderError
from app.providers import pull_state


@pytest.mark.asyncio
@respx.mock
async def test_pull_model_yields_progress_events():
    ndjson = (
        '{"status":"pulling manifest"}\n'
        '{"status":"downloading","completed":50,"total":100,"digest":"sha256:abc"}\n'
        '{"status":"downloading","completed":100,"total":100,"digest":"sha256:abc"}\n'
        '{"status":"success"}\n'
    )
    respx.post("http://ollama.test:11434/api/pull").mock(
        return_value=httpx.Response(200, content=ndjson.encode(), headers={"content-type": "application/x-ndjson"})
    )
    provider = OllamaProvider(base_url="http://ollama.test:11434", model="gemma3:4b")
    events = [e async for e in provider.pull_model()]
    assert events[-1]["status"] == "success"
    assert events[1]["completed"] == 50
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_pull_model_raises_on_error_event():
    ndjson = '{"error":"model not found"}\n'
    respx.post("http://ollama.test:11434/api/pull").mock(
        return_value=httpx.Response(200, content=ndjson.encode())
    )
    provider = OllamaProvider(base_url="http://ollama.test:11434", model="bogus:model")
    with pytest.raises(ProviderError):
        async for _ in provider.pull_model():
            pass
    await provider.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_run_pull_updates_global_state():
    ndjson = (
        '{"status":"downloading","completed":30,"total":100}\n'
        '{"status":"success"}\n'
    )
    respx.post("http://ollama.test:11434/api/pull").mock(
        return_value=httpx.Response(200, content=ndjson.encode())
    )
    provider = OllamaProvider(base_url="http://ollama.test:11434", model="gemma3:4b")
    await pull_state.run_pull(provider, "gemma3:4b")

    assert pull_state.current_pull.done is True
    assert pull_state.current_pull.active is False
    assert pull_state.current_pull.error is None
    await provider.aclose()
