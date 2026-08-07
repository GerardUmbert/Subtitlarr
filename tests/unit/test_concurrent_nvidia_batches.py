import asyncio
from datetime import timedelta

import pytest
import srt

from app.engine.translator import NVIDIA_CONCURRENT_BATCH_WINDOW, _translate_batches
from app.providers.base import ProviderStatus, TranslationProvider


def _cue(index: int, content: str) -> srt.Subtitle:
    return srt.Subtitle(
        index=index,
        start=timedelta(seconds=index),
        end=timedelta(seconds=index + 1),
        content=content,
    )


def _batches(n: int) -> list[list[srt.Subtitle]]:
    """One cue per batch, indices 1..n — simplest shape to verify ordering."""
    return [[_cue(i, f"Line {i}")] for i in range(1, n + 1)]


def _cascade(*providers) -> list:
    """_translate_batches now takes ONE ordered cascade list (cascade[0] is
    primary, cascade[1:] are fallback candidates in order) instead of
    separate active_provider/fallback_provider params — this is the direct
    replacement for the old (provider, fallback) two-arg call shape."""
    return list(providers)


class TrackingProvider(TranslationProvider):
    """Records concurrency (max simultaneous in-flight calls) and can
    resolve out of order — later-started calls finish FIRST — to prove
    ordering survives regardless of arrival order."""

    def __init__(self, name: str, delays: dict[int, float] | None = None):
        self.name = name
        self.provider_type = name
        self.model = "test-model"
        self._delays = delays or {}
        self.in_flight = 0
        self.max_in_flight = 0
        self.call_order: list[int] = []

    async def translate(
        self, dialogue_text: str, source_lang: str, target_lang: str,
        catalan_vegeta_insults: bool = False, language_variants: dict | None = None,
    ) -> str:
        index = int(dialogue_text.split("\n", 1)[0])
        self.call_order.append(index)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self._delays.get(index, 0))
            return f"{index}\nTranslated {index}"
        finally:
            self.in_flight -= 1

    async def test_connection(self) -> ProviderStatus:
        return ProviderStatus(ok=True)


@pytest.mark.asyncio
async def test_nvidia_runs_batches_concurrently_up_to_the_window():
    """NVIDIA_CONCURRENT_BATCH_WINDOW batches must actually run
    concurrently, not sequentially — confirmed by tracking simultaneous
    in-flight calls, not just call count."""
    provider = TrackingProvider("nvidia", delays={i: 0.05 for i in range(1, 9)})
    batches = _batches(8)  # 2 full windows at window size 4

    await _translate_batches(batches, "en", "es", _cascade(provider), item_id=1)

    assert provider.max_in_flight == NVIDIA_CONCURRENT_BATCH_WINDOW


@pytest.mark.asyncio
async def test_non_concurrent_providers_stay_strictly_sequential():
    """Ollama/Gemini must never see more than 1 in-flight request — this
    is the core guarantee that concurrency is opt-in per cloud provider
    (_CONCURRENT_PROVIDERS), per explicit user instruction ('not ollama'),
    not a blanket default."""
    provider = TrackingProvider("ollama", delays={i: 0.02 for i in range(1, 6)})
    batches = _batches(5)

    await _translate_batches(batches, "en", "es", _cascade(provider), item_id=1)

    assert provider.max_in_flight == 1


@pytest.mark.asyncio
async def test_openrouter_runs_batches_concurrently_up_to_the_window():
    """OpenRouter gets the same windowed-concurrency treatment as NVIDIA
    (see _CONCURRENT_PROVIDERS in translator.py) — this was a gap where
    OpenRouter silently stayed sequential even after being added as a
    cloud provider."""
    provider = TrackingProvider("openrouter", delays={i: 0.05 for i in range(1, 9)})
    batches = _batches(8)

    await _translate_batches(
        batches, "en", "es", _cascade(provider), item_id=1, concurrent_batch_window=4,
    )

    assert provider.max_in_flight == 4


@pytest.mark.asyncio
async def test_concurrent_batches_preserve_original_order_despite_out_of_order_completion():
    """Regression test for the user's explicit ordering concern: batches
    that finish OUT of order (a later-started one resolves first) must
    still be reassembled in the original cue order. Batch 1 is given the
    LONGEST delay so it resolves LAST despite starting first."""
    provider = TrackingProvider("nvidia", delays={1: 0.15, 2: 0.05, 3: 0.03, 4: 0.01})
    batches = _batches(4)

    translated_subs, engine_used, model_used = await _translate_batches(
        batches, "en", "es", _cascade(provider), item_id=1
    )

    assert [s.index for s in translated_subs] == [1, 2, 3, 4]
    assert [s.content for s in translated_subs] == [
        "Translated 1", "Translated 2", "Translated 3", "Translated 4",
    ]
    assert engine_used == "nvidia"


@pytest.mark.asyncio
async def test_nvidia_windows_dont_exceed_batch_count_for_small_items():
    """A single-batch (or under-window) item must not behave any
    differently — no crash, no extra calls, from the windowing logic on a
    small input."""
    provider = TrackingProvider("nvidia")
    batches = _batches(1)

    translated_subs, _, _ = await _translate_batches(batches, "en", "es", _cascade(provider), item_id=1)

    assert len(translated_subs) == 1
    assert provider.call_order == [1]


@pytest.mark.asyncio
async def test_nvidia_falls_back_per_batch_on_rate_limit_within_a_window():
    """A single batch hitting a rate limit inside a concurrent window must
    fall back independently — same per-batch fallback behavior as the
    sequential path, not something the whole window needs to coordinate."""
    from app.providers.base import ProviderRateLimitedError

    class FlakyProvider(TranslationProvider):
        name = "nvidia"
        provider_type = "nvidia"
        model = "test-model"

        async def translate(self, dialogue_text, source_lang, target_lang, catalan_vegeta_insults=False, language_variants=None):
            index = int(dialogue_text.split("\n", 1)[0])
            if index == 2:
                raise ProviderRateLimitedError("simulated 429")
            return f"{index}\nOK {index}"

        async def test_connection(self):
            return ProviderStatus(ok=True)

    fallback = TrackingProvider("gemini")
    batches = _batches(4)

    translated_subs, engine_used, model_used = await _translate_batches(
        batches, "en", "es", _cascade(FlakyProvider(), fallback), item_id=1
    )

    assert len(translated_subs) == 4
    assert fallback.call_order == [2]  # only the rate-limited batch fell back


@pytest.mark.asyncio
async def test_transient_failure_retries_same_provider_before_falling_back():
    """A transient failure (429, timeout, or a 5xx like 504/529) gets ONE
    retry against the SAME provider first — many real outages clear within
    seconds, so falling back to a different engine immediately would be
    premature. Only if the retry ALSO fails does it fall back."""
    from app.providers.base import ProviderRateLimitedError

    class FailsOnceProvider(TranslationProvider):
        name = "nvidia"
        provider_type = "nvidia"
        model = "test-model"

        def __init__(self):
            self.attempts: list[int] = []

        async def translate(self, dialogue_text, source_lang, target_lang, catalan_vegeta_insults=False, language_variants=None):
            index = int(dialogue_text.split("\n", 1)[0])
            self.attempts.append(index)
            if index == 2 and self.attempts.count(2) == 1:
                raise ProviderRateLimitedError("simulated transient 504")
            return f"{index}\nOK {index}"

        async def test_connection(self):
            return ProviderStatus(ok=True)

    provider = FailsOnceProvider()
    fallback = TrackingProvider("gemini")
    batches = _batches(4)

    translated_subs, engine_used, model_used = await _translate_batches(
        batches, "en", "es", _cascade(provider, fallback), item_id=1
    )

    assert len(translated_subs) == 4
    assert provider.attempts.count(2) == 2  # original attempt + one retry
    assert fallback.call_order == []  # never needed — the retry succeeded
    assert engine_used == "nvidia"


@pytest.mark.asyncio
async def test_retry_and_fallback_emit_run_events():
    """The toast-notification pipeline (run_events.py) must see a
    'retrying' event immediately on failure, then either
    'retry_succeeded' or 'fell_back' depending on how the retry goes."""
    from app.engine import run_events
    from app.providers.base import ProviderRateLimitedError

    run_events._events.clear()

    class RecoversOnRetryProvider(TranslationProvider):
        name = "nvidia"
        provider_type = "nvidia"
        model = "test-model"

        def __init__(self):
            self.attempts: list[int] = []

        async def translate(self, dialogue_text, source_lang, target_lang, catalan_vegeta_insults=False, language_variants=None):
            index = int(dialogue_text.split("\n", 1)[0])
            self.attempts.append(index)
            if index == 1 and self.attempts.count(1) == 1:
                raise ProviderRateLimitedError("simulated 504")
            return f"{index}\nOK {index}"

        async def test_connection(self):
            return ProviderStatus(ok=True)

    class AlwaysFailsProvider(TranslationProvider):
        name = "nvidia"
        provider_type = "nvidia"
        model = "test-model"

        async def translate(self, dialogue_text, source_lang, target_lang, catalan_vegeta_insults=False, language_variants=None):
            raise ProviderRateLimitedError("simulated persistent 504")

        async def test_connection(self):
            return ProviderStatus(ok=True)

    # Case 1: retry succeeds
    await _translate_batches(
        _batches(1), "en", "es", _cascade(RecoversOnRetryProvider()), item_id=1, run_id=42,
    )
    events = run_events.events_since(0)
    types = [e.event_type for e in events]
    assert types == ["retrying", "retry_succeeded"]
    assert events[0].run_id == 42
    assert events[0].batch_index == 1
    assert events[0].batch_total == 1

    run_events._events.clear()

    # Case 2: retry also fails, falls back
    fallback = TrackingProvider("gemini")
    await _translate_batches(
        _batches(1), "en", "es", _cascade(AlwaysFailsProvider(), fallback), item_id=1, run_id=42,
    )
    events = run_events.events_since(0)
    types = [e.event_type for e in events]
    assert types == ["retrying", "fell_back"]

    run_events._events.clear()


@pytest.mark.asyncio
async def test_content_blocked_falls_back_immediately_without_same_provider_retry():
    """Regression test: a content-policy block (e.g. Gemini's
    PROHIBITED_CONTENT) previously had NO fallback path at all — it's a
    plain ProviderError, which the runner doesn't retry or fall back on.
    Confirmed live: a real batch failed outright with a fallback engine
    configured but never even attempted. ProviderContentBlockedError must
    go straight to the fallback provider, WITHOUT retrying the same
    provider first (unlike ProviderRateLimitedError) — retrying a content
    block can't possibly succeed, the content didn't change."""
    from app.providers.base import ProviderContentBlockedError

    class AlwaysBlocksProvider(TranslationProvider):
        name = "gemini"
        provider_type = "gemini"
        model = "test-model"

        def __init__(self):
            self.attempts: list[int] = []

        async def translate(self, dialogue_text, source_lang, target_lang, catalan_vegeta_insults=False, language_variants=None):
            index = int(dialogue_text.split("\n", 1)[0])
            self.attempts.append(index)
            raise ProviderContentBlockedError("blocked: PROHIBITED_CONTENT")

        async def test_connection(self):
            return ProviderStatus(ok=True)

    provider = AlwaysBlocksProvider()
    fallback = TrackingProvider("nvidia")
    batches = _batches(1)

    translated_subs, engine_used, model_used = await _translate_batches(
        batches, "en", "es", _cascade(provider, fallback), item_id=1
    )

    assert len(translated_subs) == 1
    assert provider.attempts == [1]  # exactly ONE attempt — no same-provider retry
    assert fallback.call_order == [1]  # fallback was actually used
    assert engine_used == "nvidia"


@pytest.mark.asyncio
async def test_content_blocked_reraises_when_no_fallback_configured():
    """Without a fallback provider, a content block must still surface as
    a real failure (not silently swallowed) — same expectation as any
    other unrecoverable ProviderError."""
    from app.providers.base import ProviderContentBlockedError

    class AlwaysBlocksProvider(TranslationProvider):
        name = "gemini"
        provider_type = "gemini"
        model = "test-model"

        async def translate(self, dialogue_text, source_lang, target_lang, catalan_vegeta_insults=False, language_variants=None):
            raise ProviderContentBlockedError("blocked: PROHIBITED_CONTENT")

        async def test_connection(self):
            return ProviderStatus(ok=True)

    with pytest.raises(ProviderContentBlockedError):
        await _translate_batches(_batches(1), "en", "es", _cascade(AlwaysBlocksProvider()), item_id=1)


def _repetition_loop_batch() -> list[list[srt.Subtitle]]:
    """12 cues with distinct source content, all in ONE batch — enough to
    trip MAX_CONSECUTIVE_REPEATS (10) once a provider echoes the same
    translated line for every cue."""
    return [[_cue(i, f"Original distinct line {i}") for i in range(1, 13)]]


class RepetitionLoopProvider(TranslationProvider):
    """Returns a real 200-equivalent response (translate() succeeds, no
    exception) but the SAME translated line for every cue — reproduces a
    real degenerate-generation failure mode, distinct from a provider-level
    error. reassemble() is what catches this, not translate() itself."""

    def __init__(self, name: str):
        self.name = name
        self.provider_type = name
        self.model = "test-model"
        self.attempts = 0

    async def translate(self, dialogue_text, source_lang, target_lang, catalan_vegeta_insults=False, language_variants=None):
        self.attempts += 1
        indices = [int(line) for line in dialogue_text.splitlines() if line.strip().isdigit()]
        return "\n".join(f"{i}\nRepeated identical translated line" for i in indices)

    async def test_connection(self):
        return ProviderStatus(ok=True)


class GoodTranslationProvider(TranslationProvider):
    """Translates every cue distinctly and correctly — the healthy
    fallback a repetition-loop-producing provider should hand off to."""

    def __init__(self, name: str):
        self.name = name
        self.provider_type = name
        self.model = "test-model"
        self.attempts = 0
        self.call_order: list[int] = []

    async def translate(self, dialogue_text, source_lang, target_lang, catalan_vegeta_insults=False, language_variants=None):
        self.attempts += 1
        indices = [int(line) for line in dialogue_text.splitlines() if line.strip().isdigit()]
        self.call_order.extend(indices)
        return "\n".join(f"{i}\nTranslated {i}" for i in indices)

    async def test_connection(self):
        return ProviderStatus(ok=True)


@pytest.mark.asyncio
async def test_repetition_loop_falls_back_to_a_different_provider():
    """Regression test: a repetition loop is detected by reassemble()
    AFTER translate() already returned successfully (real text, no
    provider-level exception) — so it previously bypassed the
    ProviderRateLimitedError/ProviderContentBlockedError fallback handling
    entirely and failed the item outright even with a working fallback
    engine configured. Confirmed live: several items failed this way on
    Gemini with Ollama configured as fallback but never attempted."""
    provider = RepetitionLoopProvider("gemini")
    fallback = GoodTranslationProvider("nvidia")

    translated_subs, engine_used, model_used = await _translate_batches(
        _repetition_loop_batch(), "en", "es", _cascade(provider, fallback), item_id=1
    )

    assert provider.attempts == 1  # no same-provider retry — reproducible failure
    assert fallback.attempts == 1  # fallback was actually used
    assert engine_used == "nvidia"
    assert len(translated_subs) == 12


@pytest.mark.asyncio
async def test_repetition_loop_reraises_when_no_fallback_configured():
    """Without a fallback provider, a repetition loop must still surface
    as a real failure, same as any other unrecoverable reconciliation
    error."""
    from app.subtitles.reconciler import TranslationAlignmentError

    provider = RepetitionLoopProvider("gemini")

    with pytest.raises(TranslationAlignmentError):
        await _translate_batches(_repetition_loop_batch(), "en", "es", _cascade(provider), item_id=1)


@pytest.mark.asyncio
async def test_repetition_loop_falls_back_to_a_same_named_but_distinct_cascade_instance():
    """The cascade walks strictly by POSITION, not by provider name — two
    engine_instances rows can legitimately share a display name (or even
    provider type, e.g. two separately-configured Gemini keys), and each
    is still its own independently-tried instance. Confirmed: the second
    cascade slot IS attempted here even though it happens to share a name
    with the first, since it's a genuinely different (and, in this test,
    healthier) instance — this only changed from the old active_engine/
    fallback_engine model, where a same-NAME fallback was assumed to be a
    misconfiguration and skipped; a same-name-but-distinct cascade entry
    is now a legitimate, deliberate setup (see
    plans/multiple-engine-instances-cascade.md)."""
    provider = RepetitionLoopProvider("gemini")
    fallback = GoodTranslationProvider("gemini")  # same name, different (healthy) instance

    translated_subs, engine_used, model_used = await _translate_batches(
        _repetition_loop_batch(), "en", "es", _cascade(provider, fallback), item_id=1
    )

    assert fallback.attempts == 1  # the second cascade slot WAS tried
    assert engine_used == "gemini"
    assert len(translated_subs) == 12


@pytest.mark.asyncio
async def test_repetition_loop_never_retries_the_same_cascade_position_twice():
    """A repetition loop is reproducible on the SAME instance — the
    cascade must move strictly forward (never revisit cascade[0] after it
    already produced the bad output), even though nothing here prevents
    trying a distinct LATER instance that happens to share its name (see
    the test above)."""
    from app.subtitles.reconciler import TranslationAlignmentError

    provider = RepetitionLoopProvider("gemini")

    with pytest.raises(TranslationAlignmentError):
        await _translate_batches(_repetition_loop_batch(), "en", "es", _cascade(provider), item_id=1)

    assert provider.attempts == 1  # never called again after the first failure
