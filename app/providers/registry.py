from app.config import Settings
from app.providers.base import TranslationProvider
from app.providers.gemini_provider import GeminiProvider
from app.providers.groq_provider import GroqProvider
from app.providers.llamacpp_provider import LlamaCppProvider
from app.providers.nvidia_provider import NvidiaProvider
from app.providers.ollama_provider import OllamaProvider
from app.providers.openrouter_provider import OpenRouterProvider

# Only Ollama, Gemini, NVIDIA, OpenRouter, Groq, and llama.cpp are
# instantiable in v1. OpenAI/Anthropic/Grok exist as stub classes (see
# their modules) proving the interface needs no rework to add them later
# — they're intentionally left out of this factory map.
_FACTORIES = {"ollama", "gemini", "nvidia", "openrouter", "groq", "llamacpp"}


def _build(name: str, settings: Settings) -> TranslationProvider:
    if name == "ollama":
        return OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            num_ctx=settings.ollama_num_ctx,
        )
    if name == "gemini":
        return GeminiProvider(api_key=settings.gemini_api_key, model=settings.gemini_model)
    if name == "nvidia":
        return NvidiaProvider(api_key=settings.nvidia_api_key, model=settings.nvidia_model)
    if name == "openrouter":
        return OpenRouterProvider(
            api_key=settings.openrouter_api_key, model=settings.openrouter_model
        )
    if name == "groq":
        return GroqProvider(api_key=settings.groq_api_key, model=settings.groq_model)
    if name == "llamacpp":
        return LlamaCppProvider(
            base_url=settings.llamacpp_base_url,
            api_key=settings.llamacpp_api_key or None,
            model=settings.llamacpp_model or None,
        )
    raise ValueError(f"Unknown or unimplemented provider: {name!r}")


def get_active_provider(settings: Settings) -> TranslationProvider:
    return _build(settings.active_engine, settings)


def get_fallback_provider(settings: Settings) -> TranslationProvider | None:
    if not settings.fallback_engine:
        return None
    if settings.fallback_engine == settings.active_engine:
        return None
    return _build(settings.fallback_engine, settings)
