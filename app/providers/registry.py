from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import TranslationProvider
from app.providers.gemini_provider import GeminiProvider
from app.providers.groq_provider import GroqProvider
from app.providers.llamacpp_provider import LlamaCppProvider
from app.providers.nvidia_provider import NvidiaProvider
from app.providers.ollama_provider import OllamaProvider
from app.providers.openrouter_provider import OpenRouterProvider

# Ollama, Gemini, NVIDIA, OpenRouter, Groq, llama.cpp, and Anthropic are
# instantiable. OpenAI/Grok still exist as stub classes (see their
# modules) proving the interface needs no rework to add them later —
# they're intentionally left out of this factory map.
_FACTORIES = {"ollama", "gemini", "nvidia", "openrouter", "groq", "llamacpp", "anthropic"}

# Cloud providers get windowed concurrency (see translator._CONCURRENT_
# PROVIDERS, which mirrors this same set) — local providers (Ollama,
# llama.cpp) don't, since concurrent requests would just serialize
# against the same GPU/model instance anyway.
CONCURRENT_PROVIDER_TYPES = {"nvidia", "openrouter", "groq", "gemini", "anthropic"}

# Each provider_type's config dict defaults — used both to fill in a
# freshly-created instance's config and to validate/coerce what's read
# back out of an existing one. batch_token_budget/concurrent_batch_window
# used to live in runner.py's _CLOUD_ENGINE_SETTINGS/_LOCAL_ENGINE_BATCH_
# BUDGETS lookup tables, keyed by provider name; now they're just part of
# each INSTANCE's own config, since a run's cascade can mix several
# instances of different types with different tuning.
# A low default (not each provider's own unset-default, typically ~1.0,
# tuned for general chat/creative use) — subtitle translation wants
# literal, consistent output that reliably follows the rigid index/format
# instructions, not creative variation. Confirmed live: gemini-3.5-flash-
# lite silently echoed an entire batch's source English text back
# unchanged instead of translating to the requested target language, a
# failure mode a lower temperature is meant to reduce (though not
# guaranteed to eliminate — a model that ignores instructions at any
# temperature is still a model that ignores instructions).
DEFAULT_TEMPERATURE = 0.2

DEFAULT_CONFIG_BY_TYPE: dict[str, dict] = {
    "ollama": {
        "base_url": "http://localhost:11434",
        "model": "gemma3:4b",
        "num_ctx": 8192,
        "batch_token_budget": 400,
        "temperature": DEFAULT_TEMPERATURE,
    },
    "llamacpp": {
        "base_url": "http://localhost:8080",
        "model": "gemma3:4b",
        "api_key": "",
        "batch_token_budget": 400,
        "temperature": DEFAULT_TEMPERATURE,
    },
    "gemini": {
        "api_key": "",
        "model": "gemini-3.5-flash-lite",
        "batch_token_budget": 4000,
        "concurrent_batch_window": 3,
        "temperature": DEFAULT_TEMPERATURE,
    },
    "nvidia": {
        "api_key": "",
        "model": "deepseek-ai/deepseek-v4-flash",
        "batch_token_budget": 700,
        "concurrent_batch_window": 4,
        "temperature": DEFAULT_TEMPERATURE,
    },
    "openrouter": {
        "api_key": "",
        "model": "google/gemma-4-26b-a4b-it:free",
        "batch_token_budget": 4000,
        "concurrent_batch_window": 4,
        "temperature": DEFAULT_TEMPERATURE,
    },
    "groq": {
        "api_key": "",
        "model": "llama-3.1-8b-instant",
        "batch_token_budget": 1800,
        "concurrent_batch_window": 1,
        "temperature": DEFAULT_TEMPERATURE,
    },
    "anthropic": {
        "api_key": "",
        "model": "claude-haiku-4-5-20251001",
        "batch_token_budget": 4000,
        "concurrent_batch_window": 3,
        "temperature": DEFAULT_TEMPERATURE,
    },
}


# Every provider here (Gemini, and the four OpenAI-compatible ones) uses
# this exact range in its own API — Gemini confirmed live, rejecting 3.0
# with "temperature must be in the range [0.0, 2.0]". The HTML <input
# max="2"> in the UI is only a soft hint (a user can still type/scroll
# past it), so this is the actual enforcement point.
TEMPERATURE_MIN = 0.0
TEMPERATURE_MAX = 2.0


def validate_temperature(value: float | None) -> None:
    """Raises ValueError if value is outside the range every provider's
    own API accepts. None (not set) is always fine — it means "use the
    provider's default," never sent as an explicit out-of-range number."""
    if value is None:
        return
    if not (TEMPERATURE_MIN <= value <= TEMPERATURE_MAX):
        raise ValueError(
            f"temperature must be between {TEMPERATURE_MIN} and {TEMPERATURE_MAX}, got {value}"
        )


def build_provider(
    provider_type: str, config: dict, *, instance_name: str | None = None
) -> TranslationProvider:
    """Constructs a provider from an engine_instances row's config_json
    (already parsed into a dict) — the generalized replacement for the
    old _build(name, settings) which read from global Settings fields.
    instance_name, when given, becomes the provider's OWN name (shown in
    logs/DB/UI) instead of the provider_type default — lets several
    instances of the same provider_type stay distinguishable."""
    temperature = config.get("temperature", DEFAULT_TEMPERATURE)
    if provider_type == "ollama":
        provider = OllamaProvider(
            base_url=config["base_url"],
            model=config["model"],
            num_ctx=config.get("num_ctx", 8192),
            temperature=temperature,
            instance_name=instance_name,
        )
    elif provider_type == "gemini":
        provider = GeminiProvider(
            api_key=config["api_key"], model=config["model"], temperature=temperature,
            instance_name=instance_name,
        )
    elif provider_type == "nvidia":
        provider = NvidiaProvider(
            api_key=config["api_key"], model=config["model"], temperature=temperature,
            instance_name=instance_name,
        )
    elif provider_type == "openrouter":
        provider = OpenRouterProvider(
            api_key=config["api_key"], model=config["model"], temperature=temperature,
            instance_name=instance_name,
        )
    elif provider_type == "groq":
        provider = GroqProvider(
            api_key=config["api_key"], model=config["model"], temperature=temperature,
            instance_name=instance_name,
        )
    elif provider_type == "anthropic":
        provider = AnthropicProvider(
            api_key=config["api_key"], model=config["model"], temperature=temperature,
            instance_name=instance_name,
        )
    elif provider_type == "llamacpp":
        provider = LlamaCppProvider(
            base_url=config["base_url"],
            api_key=config.get("api_key") or None,
            model=config.get("model") or None,
            temperature=temperature,
            instance_name=instance_name,
        )
    else:
        raise ValueError(f"Unknown or unimplemented provider type: {provider_type!r}")
    provider.batch_token_budget = config.get("batch_token_budget", 0)
    return provider


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
