from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ProviderStatus:
    ok: bool
    detail: str = ""


class ProviderError(Exception):
    """Raised for non-retryable provider failures.

    `str(exc)` (the message) should stay short and human-readable — it's
    what gets shown directly in the Queue/History tables. `raw_detail`,
    when set, carries the full underlying detail (e.g. the provider's raw
    JSON response) that's too long/technical for a table cell but useful
    in a "show full error" expansion — callers that want it must fetch it
    separately (see item_run_log.error_detail), it is NOT automatically
    included in str(exc)."""

    def __init__(self, message: str, raw_detail: str | None = None):
        super().__init__(message)
        self.raw_detail = raw_detail


class ProviderContentBlockedError(ProviderError):
    """The provider refused to translate this specific content — a safety/
    content-policy filter, not a rate limit, outage, or malformed request.
    Retrying the SAME provider is pointless (the content won't stop
    tripping its filter), but a DIFFERENT provider's filter may not flag
    the same content, or may not have one at all — so the runner treats
    this as fallback-eligible, same as a retryable error, just skipping
    the same-provider retry step since it cannot possibly help.

    Confirmed live: Gemini blocked several real subtitle batches (a
    raunchy sitcom) with promptFeedback.blockReason /
    candidate.finishReason == PROHIBITED_CONTENT — those items simply
    failed outright with no fallback attempt, even with a fallback engine
    configured, since ProviderError normally isn't retried at all."""


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
    # `name` is the user-facing display/log name — defaults to
    # provider_type but can be overridden per-instance (e.g. "Gemini
    # (main)" vs "Gemini (backup)" for two instances of the same type),
    # since engine_instances lets several instances share one provider
    # type. `provider_type` is the fixed factory key ("gemini", "nvidia",
    # ...) and must NEVER be overridden — code that needs to know what
    # KIND of provider this is (concurrency/windowing behavior, prompt
    # quirks) keys off provider_type, never off the possibly-customized
    # name.
    name: str
    provider_type: str

    @abstractmethod
    async def translate(
        self,
        dialogue_text: str,
        source_lang: str,
        target_lang: str,
        catalan_vegeta_insults: bool = False,
        european_spanish: bool = True,
    ) -> str:
        """Sends dialogue-only text (index + content, no timestamps) to the
        LLM and returns its raw text response. Reassembly onto original
        timing happens separately in app.subtitles.reconciler.
        catalan_vegeta_insults only has an effect when target_lang is
        Catalan; european_spanish only has an effect when target_lang is
        Spanish — see app.providers.prompts. european_spanish defaults to
        True (opt-out, not opt-in), unlike catalan_vegeta_insults."""
        ...

    @abstractmethod
    async def test_connection(self) -> ProviderStatus:
        ...
