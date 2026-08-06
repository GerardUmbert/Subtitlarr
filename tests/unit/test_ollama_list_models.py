import httpx
import pytest
import respx

from app.providers.ollama_provider import OllamaProvider


@pytest.mark.asyncio
@respx.mock
async def test_list_models_returns_installed_models():
    respx.get("http://ollama.test:11434/api/tags").mock(
        return_value=httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "gemma3:4b",
                        "size": 3338801804,
                        "details": {"parameter_size": "4.3B", "quantization_level": "Q4_K_M"},
                    },
                    {
                        "name": "gemma3:12b",
                        "size": 8149190253,
                        "details": {"parameter_size": "12.2B", "quantization_level": "Q4_K_M"},
                    },
                ]
            },
        )
    )
    provider = OllamaProvider(base_url="http://ollama.test:11434", model="gemma3:4b")
    models = await provider.list_models()
    await provider.aclose()

    assert len(models) == 2
    assert models[0]["name"] == "gemma3:4b"
    assert models[0]["parameter_size"] == "4.3B"
    assert models[0]["quantization"] == "Q4_K_M"
    assert models[1]["name"] == "gemma3:12b"


@pytest.mark.asyncio
@respx.mock
async def test_list_models_empty_when_none_installed():
    respx.get("http://ollama.test:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": []})
    )
    provider = OllamaProvider(base_url="http://ollama.test:11434", model="gemma3:4b")
    models = await provider.list_models()
    await provider.aclose()
    assert models == []


@pytest.mark.asyncio
@respx.mock
async def test_list_models_raises_on_unreachable_server():
    respx.get("http://ollama.test:11434/api/tags").mock(
        return_value=httpx.Response(500)
    )
    provider = OllamaProvider(base_url="http://ollama.test:11434", model="gemma3:4b")
    with pytest.raises(httpx.HTTPStatusError):
        await provider.list_models()
    await provider.aclose()
