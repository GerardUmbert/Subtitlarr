from datetime import timedelta

import srt

from app.subtitles import srt_io


def _compose_and_parse(subs):
    """Round-trips through actual srt.compose/parse, since that's where the
    real ordering bug surfaced — srt.compose() re-sorts by (start, end,
    index) regardless of input list order."""
    return list(srt.parse(srt_io.compose_srt(subs).decode("utf-8")))


_TEXT = "Test disclaimer text."


def test_disclaimer_is_first_when_first_cue_starts_at_zero():
    """Regression test: a real dialogue cue starting at 0:00 (very common)
    used to tie the disclaimer's start time; srt.compose's sort then used
    `end` as a tiebreaker, and the disclaimer's 10s end lost to the real
    cue's shorter end, silently demoting the disclaimer to position 2+."""
    real = srt.Subtitle(index=1, start=timedelta(seconds=0), end=timedelta(seconds=1), content="Hello.")
    result = srt_io.with_ai_disclaimer([real], _TEXT)
    composed = _compose_and_parse(result)
    assert composed[0].content == _TEXT


def test_disclaimer_is_first_when_first_cue_starts_within_ten_seconds():
    real = srt.Subtitle(index=1, start=timedelta(seconds=3), end=timedelta(seconds=5), content="Hi.")
    result = srt_io.with_ai_disclaimer([real], _TEXT)
    composed = _compose_and_parse(result)
    assert composed[0].content == _TEXT


def test_disclaimer_is_first_when_first_cue_starts_after_ten_seconds():
    real = srt.Subtitle(index=1, start=timedelta(seconds=30), end=timedelta(seconds=32), content="Later.")
    result = srt_io.with_ai_disclaimer([real], _TEXT)
    composed = _compose_and_parse(result)
    assert composed[0].content == _TEXT
    assert composed[0].end == srt_io.DISCLAIMER_DURATION  # full 10s when there's room


def test_disclaimer_never_dropped_as_zero_length_cue():
    """srt.compose silently drops cues where start >= end — the disclaimer
    must never end up with a duration of zero, even when squeezed against
    a cue starting essentially immediately."""
    real = srt.Subtitle(index=1, start=timedelta(milliseconds=1), end=timedelta(seconds=1), content="Fast.")
    result = srt_io.with_ai_disclaimer([real], _TEXT)
    composed = _compose_and_parse(result)
    assert len(composed) == 2
    assert composed[0].content == _TEXT


def test_disclaimer_survives_with_empty_subtitle_list():
    result = srt_io.with_ai_disclaimer([], _TEXT)
    assert len(result) == 1
    assert result[0].content == _TEXT


def test_all_original_cues_preserved_after_disclaimer_insertion():
    subs = [
        srt.Subtitle(index=1, start=timedelta(seconds=i), end=timedelta(seconds=i + 1), content=f"Line {i}")
        for i in range(5)
    ]
    result = srt_io.with_ai_disclaimer(subs, _TEXT)
    composed = _compose_and_parse(result)
    assert len(composed) == 6
    assert [c.content for c in composed[1:]] == [f"Line {i}" for i in range(5)]


def test_disclaimer_text_uses_target_language_translation():
    text = srt_io.disclaimer_text("es", "English", "Spanish")
    assert text == "Subtitlarr utilizó IA para traducir esto de English a Spanish. Espera errores ocasionales."


def test_disclaimer_text_falls_back_to_english_for_unknown_language():
    text = srt_io.disclaimer_text("xx", "English", "Klingon")
    assert text == "Subtitlarr used AI to translate this from English into Klingon. Expect occasional errors."


def test_disclaimer_text_case_insensitive_lookup():
    assert srt_io.disclaimer_text("ES", "English", "Spanish") == srt_io.disclaimer_text(
        "es", "English", "Spanish"
    )
