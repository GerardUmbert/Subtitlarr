from app.providers.base import ProviderStatus, TranslationProvider


class AnthropicProvider(TranslationProvider):
    """Stub — proves the provider interface needs no rework to add Claude
    later. Not registered as instantiable in v1."""

    name = "anthropic"

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("Anthropic provider is not implemented in v1")

    async def translate(self, dialogue_text: str, source_lang: str, target_lang: str) -> str:
        raise NotImplementedError

    async def test_connection(self) -> ProviderStatus:
        raise NotImplementedError
