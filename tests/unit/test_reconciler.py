from datetime import timedelta
from pathlib import Path

import pytest
import srt

from app.subtitles import srt_io
from app.subtitles.reconciler import TranslationAlignmentError, reassemble

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _original_subs():
    raw = (FIXTURES / "sample_en.srt").read_bytes()
    return srt_io.parse_srt_bytes(raw)


def test_reassemble_happy_path_preserves_timing():
    original = _original_subs()
    llm_response = (
        "1\nHola.\n\n"
        "2\n¿Cómo estás hoy?\n\n"
        "3\nEstoy bien, gracias.\n"
    )
    result = reassemble(original, llm_response)
    assert [s.content for s in result] == ["Hola.", "¿Cómo estás hoy?", "Estoy bien, gracias."]
    # timing must be untouched, taken from the original cues
    assert [s.start for s in result] == [s.start for s in original]
    assert [s.end for s in result] == [s.end for s in original]


def test_reassemble_missing_cue_falls_back_to_original_text():
    original = _original_subs()
    # LLM only translated cues 1 and 3, dropped cue 2
    llm_response = "1\nHola.\n\n3\nEstoy bien, gracias.\n"
    result = reassemble(original, llm_response)
    assert result[0].content == "Hola."
    assert result[1].content == "How are you today?"  # untranslated fallback
    assert result[2].content == "Estoy bien, gracias."
    assert len(result) == 3  # cue count integrity preserved


def test_reassemble_raises_on_severe_misalignment():
    original = _original_subs()
    llm_response = "garbled unusable output with no recognizable index structure"
    with pytest.raises(TranslationAlignmentError):
        reassemble(original, llm_response)


def test_reassemble_single_cue_uses_response_positionally_on_index_mismatch():
    """Regression test: confirmed live that a single-cue batch's response
    can come back well-formed and correctly parseable, but with an index
    that doesn't match the original cue's index — root cause not pinned
    down, but with exactly one cue and exactly one translated block on
    each side, there's no real ambiguity about which is which."""
    original = [
        srt.Subtitle(
            index=141, start=timedelta(seconds=1), end=timedelta(seconds=3),
            content="But he must always be ready for her.",
        )
    ]
    # Model echoed a DIFFERENT index than the original cue's 141.
    llm_response = "99\nPerò sempre ha d'estar preparat per a ella."
    result = reassemble(original, llm_response)
    assert len(result) == 1
    assert result[0].index == 141  # original index preserved, not the model's
    assert result[0].content == "Però sempre ha d'estar preparat per a ella."
    assert result[0].start == original[0].start
    assert result[0].end == original[0].end


def test_reassemble_single_cue_matching_index_still_works_normally():
    """The positional fallback must not change behavior for the common
    case where the index DOES match."""
    original = [
        srt.Subtitle(
            index=5, start=timedelta(seconds=1), end=timedelta(seconds=3),
            content="Hello.",
        )
    ]
    result = reassemble(original, "5\nHola.")
    assert result[0].content == "Hola."


def test_reassemble_multi_cue_batch_uses_positional_fallback_on_full_index_shift():
    """Regression test: confirmed live on a real 20-cue batch — the model
    can return exactly as many well-formed blocks as there were original
    cues, in the same order, but under a completely different set of
    index numbers (e.g. it restarted numbering from 1 instead of
    continuing the original sequence). An exact count match with ZERO
    index overlap is strong evidence the response corresponds cue-for-cue
    despite the mislabeling, so it's used positionally rather than
    rejected outright."""
    original = [
        srt.Subtitle(index=141, start=timedelta(seconds=1), end=timedelta(seconds=2), content="One."),
        srt.Subtitle(index=142, start=timedelta(seconds=2), end=timedelta(seconds=3), content="Two."),
    ]
    llm_response = "99\nUno.\n\n100\nDos."
    result = reassemble(original, llm_response)
    assert [s.content for s in result] == ["Uno.", "Dos."]
    assert [s.index for s in result] == [141, 142]  # original indices preserved


def test_reassemble_multi_cue_batch_still_fails_on_partial_index_mismatch():
    """The positional fallback requires ZERO index overlap AND an exact
    count match — a PARTIAL mismatch (some indices match the originals,
    some don't) is genuine ambiguity about which block goes where, and
    must still fail rather than guess (once recovery drops below
    MIN_RECOVERABLE_FRACTION — a single matching index among many isn't
    enough overlap to trust the rest positionally, but also isn't the
    "some fell back to original text" case a smaller mismatch would be)."""
    original = [
        srt.Subtitle(index=i, start=timedelta(seconds=i), end=timedelta(seconds=i + 1), content=f"Line {i}")
        for i in range(1, 11)  # 10 cues
    ]
    # Only index 1 matches an original; the other 9 are shifted — 1/10
    # recovered is well under MIN_RECOVERABLE_FRACTION (0.5), and the
    # partial overlap disqualifies the positional fallback.
    llm_response = "1\nUno.\n\n" + "\n\n".join(f"{100+i}\nLinia {i}" for i in range(9))
    with pytest.raises(TranslationAlignmentError):
        reassemble(original, llm_response)


def test_reassemble_still_fails_when_counts_dont_match_even_with_no_overlap():
    """Positional fallback requires an EXACT count match — a response
    with a different number of blocks than original cues (even with zero
    index overlap) is still genuinely ambiguous and must fail."""
    original = [
        srt.Subtitle(index=1, start=timedelta(seconds=1), end=timedelta(seconds=2), content="One."),
        srt.Subtitle(index=2, start=timedelta(seconds=2), end=timedelta(seconds=3), content="Two."),
        srt.Subtitle(index=3, start=timedelta(seconds=3), end=timedelta(seconds=4), content="Three."),
    ]
    llm_response = "99\nUno.\n\n100\nDos."  # only 2 blocks for 3 original cues
    with pytest.raises(TranslationAlignmentError):
        reassemble(original, llm_response)


def test_reassemble_tolerant_of_extra_whitespace():
    original = _original_subs()
    llm_response = "\n\n1\nHola.\n\n\n2\n¿Cómo estás hoy?\n\n3\nEstoy bien, gracias.\n\n\n"
    result = reassemble(original, llm_response)
    assert [s.content for s in result] == ["Hola.", "¿Cómo estás hoy?", "Estoy bien, gracias."]


def test_reassemble_tolerant_of_markdown_code_fence():
    """Small models sometimes wrap the whole response in a ``` code block
    despite being told not to."""
    original = _original_subs()
    llm_response = (
        "```\n1\nHola.\n\n2\n¿Cómo estás hoy?\n\n3\nEstoy bien, gracias.\n```"
    )
    result = reassemble(original, llm_response)
    assert [s.content for s in result] == ["Hola.", "¿Cómo estás hoy?", "Estoy bien, gracias."]


def test_reassemble_tolerant_of_decorated_index_headers():
    """Small models sometimes decorate the index line (bold markers, list
    dashes, trailing punctuation) despite being told to use a plain number."""
    original = _original_subs()
    llm_response = "**1**\nHola.\n\n- 2\n¿Cómo estás hoy?\n\n3.\nEstoy bien, gracias.\n"
    result = reassemble(original, llm_response)
    assert [s.content for s in result] == ["Hola.", "¿Cómo estás hoy?", "Estoy bien, gracias."]


def test_reassemble_tolerant_of_labeled_language_headers():
    """A header must still resolve to just the index — text like
    'Cue 1' or similar labeling is NOT currently supported and should
    correctly fail to match (documents current behavior, not a requirement
    to support arbitrary prefixes)."""
    original = _original_subs()
    llm_response = "Cue 1\nHola.\n\nCue 2\n¿Cómo estás?\n\nCue 3\nBien.\n"
    with pytest.raises(TranslationAlignmentError):
        reassemble(original, llm_response)


def test_reassemble_tolerant_of_no_blank_line_separation():
    """Regression test for a real live failure (Fastball 2026, es->it via
    gemma3:4b): the model separated cues with single newlines only, no
    blank line between blocks, despite explicit instructions to use blank
    lines. Splitting solely on blank lines collapsed a 61-cue response into
    effectively one block, recovering only 1/61 cues."""
    original = _original_subs()
    llm_response = "1\nHola.\n2\n¿Cómo estás hoy?\n3\nEstoy bien, gracias.\n"
    result = reassemble(original, llm_response)
    assert [s.content for s in result] == ["Hola.", "¿Cómo estás hoy?", "Estoy bien, gracias."]


def test_reassemble_strips_stray_index_wrapper_tags():
    """Regression test for the same live failure: the model wrapped every
    translated line in <index>...</index> tags despite being told not to
    add any markup beyond the plain index/text format."""
    original = _original_subs()
    llm_response = (
        "1\n<index>Hola.</index>\n"
        "2\n<index>¿Cómo estás hoy?</index>\n"
        "3\n<index>Estoy bien, gracias.</index>\n"
    )
    result = reassemble(original, llm_response)
    assert [s.content for s in result] == ["Hola.", "¿Cómo estás hoy?", "Estoy bien, gracias."]


def test_reassemble_tolerant_of_literal_backslash_n_instead_of_real_newlines():
    """Regression test for a real live failure (1000 Men and Me: The Bonnie
    Blue Story, en->es via gemma3:4b, 900-token batches): the model emitted
    the ENTIRE response as literal backslash-n escape sequences ("616\\nHola
    .\\n") instead of real line breaks. The header-line regex needs actual
    newlines to find "index on its own line", so this alone caused a 0/62
    recovery despite the translated Spanish text itself being correct."""
    original = _original_subs()
    llm_response = r"1\nHola.\n\n2\n¿Cómo estás hoy?\n\n3\nEstoy bien, gracias.\n"
    result = reassemble(original, llm_response)
    assert [s.content for s in result] == ["Hola.", "¿Cómo estás hoy?", "Estoy bien, gracias."]


def test_reassemble_rejects_a_degenerate_repetition_loop():
    """Regression test for a real live failure (Bakuon!!, en->es via
    gemma3:4b): the model got stuck repeating the exact same line across 53
    CONSECUTIVE cue indices before recovering. Each repeated line parsed
    correctly and would have counted toward 'recovered' cues under the old
    logic — nothing checked whether recovered content was actually distinct
    per cue, so this garbage could have silently passed the recovery-
    fraction gate and been uploaded as a real translation."""
    original = [
        srt.Subtitle(index=i, start=timedelta(seconds=i), end=timedelta(seconds=i + 1), content=f"orig {i}")
        for i in range(1, 21)
    ]
    repeated_line = "Evita a los peatones ya que te atacan."
    blocks = [f"{i}\n{repeated_line}" for i in range(1, 21)]
    llm_response = "\n\n".join(blocks)

    with pytest.raises(TranslationAlignmentError, match="repetition loop"):
        reassemble(original, llm_response)


def test_reassemble_allows_repetition_when_source_was_already_repeated():
    """Regression test for a real live false positive (Bakuon!! 1x10,
    en->es via DeepSeek V4 Flash / NVIDIA): the REAL source SRT had 50
    CONSECUTIVE identical cues ("Seat height\\nWeight" — a HUD/spec-overlay
    quirk in the rip), and the LLM translated it correctly and identically
    every time. The original repetition check flagged this as a
    degenerate loop even though the translation was completely accurate —
    a matching repeated translation of already-repeated source content is
    expected, not evidence of hallucination."""
    original = [
        srt.Subtitle(
            index=i, start=timedelta(seconds=i), end=timedelta(seconds=i + 1),
            content="Seat height\nWeight",  # identical source content, like the real case
        )
        for i in range(1, 21)
    ]
    repeated_translation = "Altura del asiento Peso"
    blocks = [f"{i}\n{repeated_translation}" for i in range(1, 21)]
    llm_response = "\n\n".join(blocks)

    result = reassemble(original, llm_response)
    assert all(s.content == repeated_translation for s in result)


def test_reassemble_still_rejects_repetition_when_source_was_distinct():
    """Companion to the false-positive fix above: if the ORIGINAL cues were
    genuinely distinct but the translation repeats anyway, that's still a
    real degenerate-generation loop and must still be rejected."""
    original = [
        srt.Subtitle(
            index=i, start=timedelta(seconds=i), end=timedelta(seconds=i + 1),
            content=f"Distinct original line {i}",
        )
        for i in range(1, 21)
    ]
    repeated_line = "Evita a los peatones ya que te atacan."
    blocks = [f"{i}\n{repeated_line}" for i in range(1, 21)]
    llm_response = "\n\n".join(blocks)

    with pytest.raises(TranslationAlignmentError, match="repetition loop"):
        reassemble(original, llm_response)


def test_reassemble_allows_short_lines_to_legitimately_repeat():
    """Short exclamations/interjections CAN legitimately repeat in real
    dialogue (e.g. a character saying 'No!' multiple times) — only longer
    lines are checked for repetition, to avoid false positives."""
    original = [
        srt.Subtitle(index=i, start=timedelta(seconds=i), end=timedelta(seconds=i + 1), content=f"orig {i}")
        for i in range(1, 21)
    ]
    blocks = [f"{i}\nNo!" for i in range(1, 21)]
    llm_response = "\n\n".join(blocks)

    result = reassemble(original, llm_response)
    assert all(s.content == "No!" for s in result)


def test_reassemble_allows_a_few_consecutive_repeats_below_the_threshold():
    """A handful of consecutive repeats (e.g. 3-4) can be legitimate —
    only a long unbroken run should be treated as a degenerate loop."""
    original = [
        srt.Subtitle(index=i, start=timedelta(seconds=i), end=timedelta(seconds=i + 1), content=f"orig {i}")
        for i in range(1, 6)
    ]
    line = "This is a real repeated line of dialogue."
    blocks = [f"{i}\n{line}" for i in range(1, 6)]
    llm_response = "\n\n".join(blocks)

    result = reassemble(original, llm_response)
    assert all(s.content == line for s in result)


def test_reassemble_handles_multiline_content_without_blank_line_separation():
    """Cue content spanning multiple lines (common — original subtitle
    lines often wrap) must still parse correctly even without blank-line
    separation between cues, matching the real captured failure case."""
    original = _original_subs()
    llm_response = (
        "1\n<index>Prima riga\nseconda riga</index>\n"
        "2\n<index>Altra riga</index>\n"
        "3\n<index>Ultima riga</index>\n"
    )
    result = reassemble(original, llm_response)
    assert result[0].content == "Prima riga\nseconda riga"
    assert result[1].content == "Altra riga"
    assert result[2].content == "Ultima riga"
