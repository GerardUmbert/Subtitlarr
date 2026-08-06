from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ProviderStatus:
    ok: bool
    detail: str = ""


class ProviderError(Exception):
    """Raised for non-retryable provider failures."""


class ProviderRateLimitedError(Exception):
    """Raised for retryable failures (429, timeout, transient 5xx server
    errors) — the runner retries the same provider once before falling
    back to a secondary provider on this specific exception.

    retry_after_seconds overrides how long that one retry waits: a real
    429 means the per-minute rate-limit window needs to roll over, so it
    should wait close to a full minute regardless of the configured
    pause_between_items_seconds; other retryable errors (timeouts,
    transient 5xx) are usually gone within seconds, so they use the
    caller's own short pause instead. None means "use the caller's
    default"."""

    def __init__(self, message: str, retry_after_seconds: float | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class TranslationProvider(ABC):
    name: str

    @abstractmethod
    async def translate(
        self,
        dialogue_text: str,
        source_lang: str,
        target_lang: str,
        catalan_vegeta_insults: bool = False,
    ) -> str:
        """Sends dialogue-only text (index + content, no timestamps) to the
        LLM and returns its raw text response. Reassembly onto original
        timing happens separately in app.subtitles.reconciler.
        catalan_vegeta_insults only has an effect when target_lang is
        Catalan — see app.providers.prompts."""
        ...

    @abstractmethod
    async def test_connection(self) -> ProviderStatus:
        ...
