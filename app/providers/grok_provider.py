from app.providers.base import ProviderStatus, TranslationProvider


class GrokProvider(TranslationProvider):
    """Stub — proves the provider interface needs no rework to add Grok
    later. Not registered as instantiable in v1."""

    name = "grok"

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("Grok provider is not implemented in v1")

    async def translate(self, dialogue_text: str, source_lang: str, target_lang: str) -> str:
        raise NotImplementedError

    async def test_connection(self) -> ProviderStatus:
        raise NotImplementedError
