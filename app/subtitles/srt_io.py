from datetime import timedelta

import srt

from app.bazarr.schemas import SubtitleCue


def cues_from_bazarr(cues: list[SubtitleCue]) -> list[srt.Subtitle]:
    """Converts Bazarr's already-parsed subtitle cues (from
    GET /api/subtitles/contents) into srt.Subtitle objects, so the rest of
    the pipeline works with one consistent representation regardless of
    whether the source came from Bazarr's API or raw SRT bytes."""
    return [
        srt.Subtitle(
            index=cue.index,
            start=timedelta(
                hours=cue.start.hours,
                minutes=cue.start.minutes,
                seconds=cue.start.seconds,
                microseconds=cue.start.microseconds,
            ),
            end=timedelta(
                hours=cue.end.hours,
                minutes=cue.end.minutes,
                seconds=cue.end.seconds,
                microseconds=cue.end.microseconds,
            ),
            content=cue.content,
            proprietary=cue.proprietary,
        )
        for cue in cues
    ]


def parse_srt_bytes(raw: bytes) -> list[srt.Subtitle]:
    """Fallback parser for raw SRT bytes (e.g. if a future source doesn't go
    through Bazarr's contents endpoint). Tries utf-8-sig first since many
    subtitle files carry a BOM; caller should use charset-normalizer upstream
    for anything not valid UTF-8."""
    text = raw.decode("utf-8-sig")
    return list(srt.parse(text))


def extract_dialogue_text(subs: list[srt.Subtitle]) -> str:
    """Builds the LLM prompt payload: index + dialogue only, no timestamps —
    timestamps are irrelevant to translation and only risk the model
    corrupting them if included."""
    return "\n\n".join(f"{sub.index}\n{sub.content}" for sub in subs)


# Rough chars-per-token estimate for chunk sizing — actual tokenization
# varies per model/language, so this is deliberately conservative (favors
# smaller chunks) rather than exact. A full movie's dialogue can easily
# exceed a small local model's context window (e.g. gemma3:4b's default
# 4096 tokens) if sent as a single prompt; batching keeps each request well
# within budget regardless of model.
_CHARS_PER_TOKEN_ESTIMATE = 3.2


def chunk_cues(subs: list[srt.Subtitle], max_tokens_per_batch: int = 900) -> list[list[srt.Subtitle]]:
    """Splits cues into batches sized to stay within a translation request's
    token budget, without ever splitting a single cue across batches. Each
    batch is translated independently and merged back together — see
    translator.translate_item. max_tokens_per_batch is deliberately well
    under typical small-model context windows to leave room for the system
    prompt, instructions, and the model's own response tokens."""
    max_chars = int(max_tokens_per_batch * _CHARS_PER_TOKEN_ESTIMATE)
    batches: list[list[srt.Subtitle]] = []
    current: list[srt.Subtitle] = []
    current_chars = 0

    for sub in subs:
        cue_chars = len(sub.content) + len(str(sub.index)) + 2  # +2 for the "\n\n" join overhead
        if current and current_chars + cue_chars > max_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(sub)
        current_chars += cue_chars

    if current:
        batches.append(current)

    return batches


DISCLAIMER_TEXT = "Generated via Subtitlarr using AI — may contain translation errors."
DISCLAIMER_DURATION = timedelta(seconds=10)
DISCLAIMER_MIN_DURATION = timedelta(milliseconds=500)


def with_ai_disclaimer(subs: list[srt.Subtitle]) -> list[srt.Subtitle]:
    """Prepends a 10-second disclaimer cue at the very start of the file, so
    viewers know this subtitle was machine-translated rather than sourced
    from a human/official release. Deliberately does NOT shift any of the
    original cues' timing — a disclaimer briefly overlapping the first line
    of real dialogue is harmless, whereas shifting every timestamp would
    desync the whole file from the video's actual audio.

    srt.compose() re-sorts all cues by (start, end, index) regardless of
    input list order (see srt.Subtitle.__lt__) — a disclaimer ending at 10s
    would sort AFTER a real cue that starts and ends within the first 10
    seconds (common), landing it second or later instead of first. To
    guarantee first position under that exact sort key, the disclaimer's end
    is capped at the first real cue's start time when that's earlier than
    10s. It's floored at DISCLAIMER_MIN_DURATION so it's never zero-length
    (srt.compose silently drops start==end cues) — when the first real cue
    starts before that floor, the disclaimer briefly overlaps it, which is
    harmless (viewers just see two lines at once for a moment); losing or
    misordering the disclaimer is the actual failure mode being avoided."""
    disclaimer_end = DISCLAIMER_DURATION
    if subs and subs[0].start < disclaimer_end:
        disclaimer_end = max(subs[0].start, DISCLAIMER_MIN_DURATION)

    disclaimer = srt.Subtitle(
        index=1,
        start=timedelta(seconds=0),
        end=disclaimer_end,
        content=DISCLAIMER_TEXT,
    )
    renumbered = [disclaimer] + [
        srt.Subtitle(index=i + 2, start=sub.start, end=sub.end, content=sub.content, proprietary=sub.proprietary)
        for i, sub in enumerate(subs)
    ]
    return renumbered


def compose_srt(subs: list[srt.Subtitle]) -> bytes:
    return srt.compose(subs).encode("utf-8")
