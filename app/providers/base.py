from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ProviderStatus:
    ok: bool
    detail: str = ""


class ProviderError(Exception):
    """Raised for non-retryable provider failures."""


class ProviderRateLimitedError(Exception):
    """Raised for retryable failures (429, timeout) — the runner may fall
    back to a secondary provider on this specific exception."""


class TranslationProvider(ABC):
    name: str

    @abstractmethod
    async def translate(self, dialogue_text: str, source_lang: str, target_lang: str) -> str:
        """Sends dialogue-only text (index + content, no timestamps) to the
        LLM and returns its raw text response. Reassembly onto original
        timing happens separately in app.subtitles.reconciler."""
        ...

    @abstractmethod
    async def test_connection(self) -> ProviderStatus:
        ...
