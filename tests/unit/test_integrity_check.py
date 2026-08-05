from datetime import timedelta

import pytest
import srt

from app.subtitles.reconciler import TranslationIntegrityError, verify_full_file_integrity


def _sub(index, start_s, end_s, content="x"):
    return srt.Subtitle(index=index, start=timedelta(seconds=start_s), end=timedelta(seconds=end_s), content=content)


def _sample(n=3):
    return [_sub(i + 1, i, i + 1) for i in range(n)]


def test_passes_when_structurally_identical():
    original = _sample(3)
    translated = [_sub(s.index, s.start.total_seconds(), s.end.total_seconds(), "tr") for s in original]
    verify_full_file_integrity(original, translated)  # should not raise


def test_raises_on_cue_count_mismatch():
    original = _sample(3)
    translated = _sample(2)
    with pytest.raises(TranslationIntegrityError, match="Cue count mismatch"):
        verify_full_file_integrity(original, translated)


def test_raises_on_first_cue_timing_mismatch():
    original = _sample(3)
    translated = _sample(3)
    translated[0] = _sub(1, 99, 100)  # first cue timing altered
    with pytest.raises(TranslationIntegrityError, match="First cue timing mismatch"):
        verify_full_file_integrity(original, translated)


def test_raises_on_last_cue_timing_mismatch():
    original = _sample(3)
    translated = _sample(3)
    translated[-1] = _sub(3, 99, 100)  # last cue timing altered
    with pytest.raises(TranslationIntegrityError, match="Last cue timing mismatch"):
        verify_full_file_integrity(original, translated)


def test_empty_lists_pass():
    verify_full_file_integrity([], [])  # nothing to compare, should not raise


def test_detects_a_dropped_batch():
    """Regression scenario for the actual feature request: if a whole
    batch got silently dropped during merging, the cue count would be
    short and the last cue's timing would no longer match — either check
    alone would catch this, together they're a solid safety net."""
    original = _sample(10)
    translated = _sample(10)[:7]  # simulate a dropped trailing batch
    with pytest.raises(TranslationIntegrityError):
        verify_full_file_integrity(original, translated)
