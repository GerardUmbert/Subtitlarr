import logging
import re

import srt

logger = logging.getLogger(__name__)

_CUE_HEADER_RE = re.compile(r"^(\d+)\s*$", re.MULTILINE)

# If fewer than this fraction of original cues can be matched in the LLM's
# response, the translation is considered unusable rather than partially
# trusted — better to fail loudly than upload a badly mangled subtitle.
MIN_RECOVERABLE_FRACTION = 0.5

# A live run got stuck in a degenerate-generation loop, repeating the exact
# same line ("Evita a los peatones ya que te atacan.") across 53 CONSECUTIVE
# cue indices before recovering. Each repeated line parsed correctly and
# counted toward "recovered" cues — nothing previously checked whether the
# recovered content was actually distinct per cue, so this garbage could
# have silently passed the recovery-fraction gate and been uploaded. A real
# translation of genuinely different dialogue lines essentially never
# repeats the identical non-trivial line this many times in a row.
MAX_CONSECUTIVE_REPEATS = 10


class TranslationAlignmentError(Exception):
    """raw_detail carries the LLM's actual raw response text that failed to
    align — previously only logged to the server log file
    (logger.error("...Raw LLM response follows...")), invisible anywhere in
    the UI. translator.py's failure handlers already read
    getattr(exc, "raw_detail", None) into item_run_log.error_detail/
    items.error_detail (the same field the Queue/History "click for full
    error" modal displays for provider errors) — this exception just never
    populated it, so alignment failures showed no detail on screen even
    though the raw text was captured all along."""

    def __init__(self, message: str, raw_detail: str | None = None):
        super().__init__(message)
        self.raw_detail = raw_detail


class TranslationIntegrityError(Exception):
    """Raised when the fully-merged translated file doesn't structurally
    match the original source — wrong cue count, or first/last cue timing
    that doesn't line up. Since translation/reassembly never touches
    timing, any mismatch here means something went wrong in batching or
    reassembly (e.g. a batch was dropped, duplicated, or misaligned) and
    the file must not be uploaded to Bazarr."""


def verify_full_file_integrity(
    original_subs: list[srt.Subtitle], translated_subs: list[srt.Subtitle]
) -> None:
    """Sanity check run on the FULLY MERGED file (all batches combined),
    before the AI disclaimer is added — cue count and the first/last cue's
    timestamps must exactly match the original. Raises
    TranslationIntegrityError if they don't; caller should skip uploading
    entirely rather than post a malformed or incomplete subtitle."""
    if len(original_subs) != len(translated_subs):
        raise TranslationIntegrityError(
            f"Cue count mismatch: original has {len(original_subs)}, "
            f"translated has {len(translated_subs)}."
        )
    if not original_subs:
        return  # nothing to compare timing on

    orig_first, orig_last = original_subs[0], original_subs[-1]
    tr_first, tr_last = translated_subs[0], translated_subs[-1]

    if orig_first.start != tr_first.start or orig_first.end != tr_first.end:
        raise TranslationIntegrityError(
            f"First cue timing mismatch: original {orig_first.start}-{orig_first.end}, "
            f"translated {tr_first.start}-{tr_first.end}."
        )
    if orig_last.start != tr_last.start or orig_last.end != tr_last.end:
        raise TranslationIntegrityError(
            f"Last cue timing mismatch: original {orig_last.start}-{orig_last.end}, "
            f"translated {tr_last.start}-{tr_last.end}."
        )


_MARKDOWN_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*\n?|```\s*$", re.MULTILINE)
# Small models sometimes prefix an index with markdown bold/list markers
# ("**12**", "- 12", "12.") despite instructions not to — strip those
# before checking if a header line is a plain cue index.
_HEADER_DECORATION_RE = re.compile(r"^[\s\-*>#]*\**\s*(\d+)\s*\**[.:)]?\s*$")
# A live run with gemma3:4b returned every cue wrapped in <index>...</index>
# tags despite explicit instructions not to add extra markup — strip any
# XML/HTML-ish wrapper tags from translated content before use.
_WRAPPER_TAG_RE = re.compile(r"</?index\s*/?>", re.IGNORECASE)
# A live run also returned cues separated by single newlines only, not the
# blank-line separation requested — splitting solely on blank lines
# collapsed the entire 61-cue response into one block, recovering 1/61.
# A cue header line (a lone number on its own line, optionally decorated)
# is used as the actual block boundary instead, which works regardless of
# whether the model added blank lines or not.
_HEADER_LINE_RE = re.compile(r"^[\s\-*>#]*\**\s*(\d+)\s*\**[.:)]?\s*$", re.MULTILINE)
# A live run mixed BOTH formats in one response: some cues as "N\ntext"
# (matches the header-alone pattern above), others as "N. text" with the
# translated content immediately following the separator on the SAME line
# (e.g. "17. Il mio conto..."). The header-alone regex requires the whole
# line to be just the index, so it silently skipped every inline-content
# header, recovering only 20/62 cues from an otherwise well-formed response.
# This second pattern captures the index AND treats everything after the
# separator, up to end of line, as the start of that cue's content.
_HEADER_WITH_INLINE_CONTENT_RE = re.compile(
    r"^[\s\-*>#]*\**\s*(\d+)\s*\**[.:)]\s+(?=\S)", re.MULTILINE
)
# A live run returned the ENTIRE response as literal backslash-n escape
# sequences ("616\nHola.\n\n617\n...") instead of real line breaks — the
# header-line regex needs actual newlines to find "index on its own line",
# so this alone caused a 0/62 recovery despite otherwise-correct translated
# text. Normalize literal \n / \r\n text into real newlines before parsing.
_LITERAL_NEWLINE_RE = re.compile(r"\\r\\n|\\n")


def _normalize_literal_newlines(text: str) -> str:
    return _LITERAL_NEWLINE_RE.sub("\n", text)


def _strip_markdown_fences(text: str) -> str:
    return _MARKDOWN_FENCE_RE.sub("", text)


def _strip_wrapper_tags(text: str) -> str:
    return _WRAPPER_TAG_RE.sub("", text).strip()


def _find_header_matches(text: str) -> list[re.Match]:
    """Finds cue-header positions using BOTH supported formats — a header
    alone on its own line, and a header immediately followed by content on
    the same line — since a single response can mix both. Overlapping
    matches at the same position are deduplicated, keeping the header-alone
    match (its .end() sits at the true start of the next line, same as the
    inline-content pattern's .end() sits at the true start of that cue's
    text — both are valid content-start boundaries)."""
    matches = list(_HEADER_LINE_RE.finditer(text)) + list(
        _HEADER_WITH_INLINE_CONTENT_RE.finditer(text)
    )
    matches.sort(key=lambda m: m.start())
    deduped: list[re.Match] = []
    seen_starts: set[int] = set()
    for m in matches:
        if m.start() in seen_starts:
            continue
        seen_starts.add(m.start())
        deduped.append(m)
    return deduped


def _parse_llm_response(text: str) -> dict[int, str]:
    """Parses the LLM's numbered response ("<index>\\n<translated line>...")
    back into {index: translated_text}. Splits on cue-header lines directly
    rather than requiring blank-line separation between blocks, since small
    models don't reliably add the blank lines despite being told to.
    Tolerant of markdown code fences, stray wrapper tags, literal \n escape
    sequences in place of real line breaks, and headers whose content
    starts on the same line rather than the next one."""
    return {index: content for index, content in _parse_llm_response_ordered(text)}


def _parse_llm_response_ordered(text: str) -> list[tuple[int, str]]:
    """Same parsing as _parse_llm_response, but preserves response ORDER
    (a dict loses this if the model echoes duplicate or out-of-sequence
    indices) — needed for the positional-fallback path in reassemble()."""
    text = _normalize_literal_newlines(text)
    text = _strip_markdown_fences(text)
    matches = _find_header_matches(text)
    result: list[tuple[int, str]] = []
    for i, m in enumerate(matches):
        index = int(m.group(1))
        content_start = m.end()
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = _strip_wrapper_tags(text[content_start:content_end])
        if content:
            result.append((index, content))
    return result


def _detect_repetition_loop(
    original_subs: list[srt.Subtitle], translated_by_index: dict[int, str]
) -> str | None:
    """Scans recovered translations in original cue order for the same
    non-trivial content repeated too many times consecutively — a
    degenerate-generation loop, not a real translation of distinct dialogue.
    Short lines (e.g. a repeated "Yeah!" or "No!") are excluded from
    counting, since those can legitimately repeat in real dialogue; only
    longer lines are checked, where repetition is essentially never
    legitimate.

    Confirmed live: a real source SRT had 50 CONSECUTIVE identical cues
    ("Seat height\\nWeight" — a HUD/spec-overlay quirk in the rip), and the
    LLM translated it correctly and IDENTICALLY every time, which this
    check originally flagged as a false-positive degenerate loop. If the
    ORIGINAL source content was already repeated for that same run, a
    matching repeated translation is expected and correct, not evidence of
    hallucination — the check only trips when the TRANSLATED output
    repeats but the source cues underneath it did NOT.

    Returns the repeated text if a genuine loop is found, else None."""
    MIN_CONTENT_LENGTH_TO_CHECK = 15
    run_content: str | None = None
    run_length = 0
    run_source_all_identical = True
    run_source_content: str | None = None
    for sub in original_subs:
        content = translated_by_index.get(sub.index)
        if content is None or len(content) < MIN_CONTENT_LENGTH_TO_CHECK:
            run_content = None
            run_length = 0
            run_source_all_identical = True
            run_source_content = None
            continue
        if content == run_content:
            run_length += 1
            if sub.content != run_source_content:
                run_source_all_identical = False
            if run_length >= MAX_CONSECUTIVE_REPEATS and not run_source_all_identical:
                return content
        else:
            run_content = content
            run_length = 1
            run_source_all_identical = True
            run_source_content = sub.content
    return None


def reassemble(original_subs: list[srt.Subtitle], llm_response: str) -> list[srt.Subtitle]:
    """Reattaches the LLM's translated dialogue onto the ORIGINAL cue timing
    and index structure — never trusts the LLM to reproduce timestamps.
    Cues the LLM failed to translate keep their original-language text rather
    than being dropped, so cue count/timing integrity is never broken.
    Raises TranslationAlignmentError if too little of the response is usable."""
    translated_by_index = _parse_llm_response(llm_response)

    repeated_content = _detect_repetition_loop(original_subs, translated_by_index)
    if repeated_content is not None:
        logger.error(
            "Alignment failure — degenerate-generation loop detected "
            "(%d+ consecutive cues repeating the same content). "
            "Repeated content: %r. Raw LLM response follows:\n%s",
            MAX_CONSECUTIVE_REPEATS, repeated_content, llm_response,
        )
        raise TranslationAlignmentError(
            "LLM response contains a repetition loop "
            f"({MAX_CONSECUTIVE_REPEATS}+ consecutive cues with identical content); "
            "translation is too unreliable to trust.",
            raw_detail=llm_response,
        )

    # Positional fallback: if the response contains exactly as many
    # translated blocks as there are original cues, and NONE of the
    # echoed indices match the originals (a wholesale index shift/reset,
    # not a partial mismatch), match them 1:1 by order instead of by
    # index. Confirmed live: a real response came back fully-formed, in
    # order, one block per original cue, but under different index
    # numbers — root cause not pinned down, but an exact count match with
    # zero index overlap is itself strong evidence the response DOES
    # correspond cue-for-cue, just mislabeled, and rejecting it outright
    # would throw away a very likely correct translation. A PARTIAL index
    # mismatch (some match, some don't) is deliberately NOT covered here
    # — that's genuine ambiguity about which block goes where, and must
    # still fail rather than guess.
    original_indices = [sub.index for sub in original_subs]
    parsed_ordered = _parse_llm_response_ordered(llm_response)
    if (
        original_subs
        and len(parsed_ordered) == len(original_subs)
        and not (set(idx for idx, _ in parsed_ordered) & set(original_indices))
    ):
        logger.warning(
            "Positional fallback: response had %d block(s) matching the "
            "%d original cue(s) in count, but NONE of the echoed indices "
            "matched — using response order instead of index matching.",
            len(parsed_ordered), len(original_subs),
        )
        translated_by_index = {
            original_indices[i]: content for i, (_, content) in enumerate(parsed_ordered)
        }

    recovered = sum(1 for sub in original_subs if sub.index in translated_by_index)
    if original_subs and recovered / len(original_subs) < MIN_RECOVERABLE_FRACTION:
        logger.error(
            "Alignment failure — only recovered %d/%d cues. Raw LLM response follows:\n%s",
            recovered, len(original_subs), llm_response,
        )
        raise TranslationAlignmentError(
            f"Only recovered {recovered}/{len(original_subs)} cues from LLM response; "
            "translation is too misaligned to trust.",
            raw_detail=llm_response,
        )

    reassembled = []
    for sub in original_subs:
        translated = translated_by_index.get(sub.index)
        if translated is None:
            logger.warning(
                "Cue %d not found in LLM response; keeping original-language text.",
                sub.index,
            )
            reassembled.append(sub)
        else:
            reassembled.append(
                srt.Subtitle(
                    index=sub.index,
                    start=sub.start,
                    end=sub.end,
                    content=translated,
                    proprietary=sub.proprietary,
                )
            )
    return reassembled
