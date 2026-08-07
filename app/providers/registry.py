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

# Cloud providers get windowed concurrency (see translator._CONCURRENT_
# PROVIDERS, which mirrors this same set) — local providers (Ollama,
# llama.cpp) don't, since concurrent requests would just serialize
# against the same GPU/model instance anyway.
CONCURRENT_PROVIDER_TYPES = {"nvidia", "openrouter", "groq", "gemini"}

# Each provider_type's config dict defaults — used both to fill in a
# freshly-created instance's config and to validate/coerce what's read
# back out of an existing one. batch_token_budget/concurrent_batch_window
# used to live in runner.py's _CLOUD_ENGINE_SETTINGS/_LOCAL_ENGINE_BATCH_
# BUDGETS lookup tables, keyed by provider name; now they're just part of
# each INSTANCE's own config, since a run's cascade can mix several
# instances of different types with different tuning.
DEFAULT_CONFIG_BY_TYPE: dict[str, dict] = {
    "ollama": {
        "base_url": "http://localhost:11434",
        "model": "gemma3:4b",
        "num_ctx": 8192,
        "batch_token_budget": 400,
    },
    "llamacpp": {
        "base_url": "http://localhost:8080",
        "model": "gemma3:4b",
        "api_key": "",
        "batch_token_budget": 400,
    },
    "gemini": {
        "api_key": "",
        "model": "gemini-3.5-flash-lite",
        "batch_token_budget": 4000,
        "concurrent_batch_window": 3,
    },
    "nvidia": {
        "api_key": "",
        "model": "deepseek-ai/deepseek-v4-flash",
        "batch_token_budget": 700,
        "concurrent_batch_window": 4,
    },
    "openrouter": {
        "api_key": "",
        "model": "google/gemma-4-26b-a4b-it:free",
        "batch_token_budget": 4000,
        "concurrent_batch_window": 4,
    },
    "groq": {
        "api_key": "",
        "model": "llama-3.1-8b-instant",
        "batch_token_budget": 1800,
        "concurrent_batch_window": 1,
    },
}


def build_provider(
    provider_type: str, config: dict, *, instance_name: str | None = None
) -> TranslationProvider:
    """Constructs a provider from an engine_instances row's config_json
    (already parsed into a dict) — the generalized replacement for the
    old _build(name, settings) which read from global Settings fields.
    instance_name, when given, becomes the provider's OWN name (shown in
    logs/DB/UI) instead of the provider_type default — lets several
    instances of the same provider_type stay distinguishable."""
    if provider_type == "ollama":
        return OllamaProvider(
            base_url=config["base_url"],
            model=config["model"],
            num_ctx=config.get("num_ctx", 8192),
            instance_name=instance_name,
        )
    if provider_type == "gemini":
        return GeminiProvider(
            api_key=config["api_key"], model=config["model"], instance_name=instance_name
        )
    if provider_type == "nvidia":
        return NvidiaProvider(
            api_key=config["api_key"], model=config["model"], instance_name=instance_name
        )
    if provider_type == "openrouter":
        return OpenRouterProvider(
            api_key=config["api_key"], model=config["model"], instance_name=instance_name
        )
    if provider_type == "groq":
        return GroqProvider(
            api_key=config["api_key"], model=config["model"], instance_name=instance_name
        )
    if provider_type == "llamacpp":
        return LlamaCppProvider(
            base_url=config["base_url"],
            api_key=config.get("api_key") or None,
            model=config.get("model") or None,
            instance_name=instance_name,
        )
    raise ValueError(f"Unknown or unimplemented provider type: {provider_type!r}")


def batch_settings_for(config: dict) -> tuple[int, int]:
    """(batch_token_budget, concurrent_batch_window) for an instance's
    config — concurrent_batch_window is meaningless for non-concurrent
    (local) providers and defaults to 1 when absent."""
    return config.get("batch_token_budget", 0), config.get("concurrent_batch_window", 1)


def build_cascade_providers(
    instances: list[dict],
) -> tuple[list[TranslationProvider], dict[str, int]]:
    """Builds one provider per instance dict (as returned by
    engine_instances_repo.get_cascade/list_instances), in the SAME order,
    plus a {provider.name: instance_id} map so callers can report a
    rate-limit failure/success back against the right DB row after a
    translate() call (translator.py only ever sees providers, not raw
    instance dicts, by design — it shouldn't need to know about the DB
    layer at all)."""
    providers: list[TranslationProvider] = []
    name_to_id: dict[str, int] = {}
    for instance in instances:
        provider = build_provider(
            instance["provider_type"], instance["config"], instance_name=instance["name"]
        )
        providers.append(provider)
        name_to_id[provider.name] = instance["id"]
    return providers, name_to_id
