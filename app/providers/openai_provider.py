from app.providers.base import ProviderStatus, TranslationProvider


class OpenAIProvider(TranslationProvider):
    """Stub — proves the provider interface needs no rework to add OpenAI
    later. Not registered as instantiable in v1."""

    name = "openai"

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("OpenAI provider is not implemented in v1")

    async def translate(self, dialogue_text: str, source_lang: str, target_lang: str) -> str:
        raise NotImplementedError

    async def test_connection(self) -> ProviderStatus:
        raise NotImplementedError
