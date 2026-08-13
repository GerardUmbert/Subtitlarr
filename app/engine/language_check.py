"""Batched audit of already-completed translations' actual output
language, run periodically (manual button, optional cron) rather than
inline during a translation run — verify_full_file_integrity/reassemble's
alignment checks only ever verify STRUCTURE (cue count, index presence),
never whether the translated text is actually IN the target language.
Confirmed live: gemini-3.5-flash-lite returned a perfectly well-formed,
correctly-indexed response that was simply still in English for a
Catalan target — invisible to every existing check.

One LLM call covers many items at once ("here are N files' sample text,
what language is each") instead of one call per item, to avoid burning a
separate request (and RPD quota) per already-completed item during a
sweep over a large backlog.
"""

import logging
import random
import re
import sqlite3

from app import state
from app.bazarr.client import BazarrClient
from app.db import engine_instances_repo, repository
from app.engine import upload_queue
from app.providers import languages as language_names
from app.providers import registry
from app.subtitles import srt_io

logger = logging.getLogger(__name__)

# How many cues' worth of text to sample per item for the check — the
# whole file is unnecessary (and wasteful of tokens) just to identify
# what language it's in; a handful of substantial lines is plenty for an
# LLM to confidently name the language, same reasoning as human language
# identification not needing the whole document.
SAMPLE_CUE_COUNT = 8
# Word count, not character count: a name-plus-honorific line like
# "(Kitagawa Marin) Gojo-kun?" is 27 characters but carries almost no
# real language signal and is disproportionately likely to be a proper
# noun — confirmed live: "My Dress-Up Darling" 1x10 sampled mostly
# name/honorific lines and got flagged as Japanese despite being a
# genuinely, fully translated Italian file. Requiring more actual words
# biases sampling toward real sentences instead.
MIN_SAMPLE_LINE_WORDS = 4

# Intros (OP songs, often left in romanized Japanese by design — a
# karaoke convention, not a translation failure) and outros (ED songs,
# credits, "next episode" previews) both cluster at the very start/end of
# an episode. Rather than a small fixed buffer, sample from the middle
# third of the file's cues — comfortably clear of both ends for any
# normal-length episode, and still leaves plenty of real dialogue to
# sample from (the body of the episode). Only applied when the file has
# enough cues that a middle third is meaningfully larger than
# SAMPLE_CUE_COUNT; short files fall back to using the whole file.
MIDDLE_THIRD_MIN_CUES = 3 * SAMPLE_CUE_COUNT

_RESULT_LINE_RE = re.compile(r"^\s*(\d+)\s*[:.\-]\s*(.+?)\s*$")


class LanguageCheckError(Exception):
    """Raised for check-level failures (no engine configured, no items to
    check) — distinct from a per-item parse miss, which is just recorded
    as that one item staying 'unchecked' rather than aborting the sweep."""


async def _get_translated_text(
    conn: sqlite3.Connection, client: BazarrClient, item: sqlite3.Row
) -> str | None:
    """The actual translated text currently sitting for this item — from
    Bazarr for an already-uploaded 'done' item, or from the local queue
    file for a still-queued 'translated_pending_upload' one. None if
    neither is found (e.g. manually deleted from Bazarr since), which
    just skips that item for this sweep rather than failing the batch."""
    if item["status"] == "translated_pending_upload":
        path = upload_queue.DEFAULT_QUEUE_ROOT / f"{item['id']}.srt"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    # 'done' — fetch whatever Bazarr currently has for this item's own
    # target_language track, same lookup pattern used throughout
    # (app.engine.compare, translator.py) rather than trusting a locally
    # cached copy that may be stale or never existed.
    if item["item_type"] == "episode":
        detail = await client.get_episode_detail(item["bazarr_id"])
    else:
        detail = await client.get_movie_detail(item["bazarr_id"])
    if detail is None:
        return None
    match = next(
        (s for s in detail.subtitles if s.code2 == item["target_language"] and s.path and not s.forced),
        None,
    )
    if match is None:
        return None
    cues = await client.get_subtitle_contents(match.path)
    if not cues:
        return None
    return srt_io.compose_srt(srt_io.cues_from_bazarr(cues)).decode("utf-8")


_TIMESTAMP_RE = re.compile(
    r"^(\d\d:\d\d:\d\d,\d\d\d)\s*-->\s*(\d\d:\d\d:\d\d,\d\d\d)"
)


def _sample_text(full_text: str) -> str:
    """A handful of substantial cues' content, stripped of index/timestamp
    scaffolding — just the dialogue itself, which is all the language
    audit needs and keeps the batched prompt compact.

    Skips any cue that mentions "Subtitlarr": with_ai_disclaimer() prepends
    an AI-disclaimer cue to every generated file (see app.subtitles.srt_io),
    and every translated variant of it (all 187 in
    disclaimer_translations.json, confirmed) still contains the product
    name literally, untranslated, since it's a proper noun. Sampling that
    line would feed the check a cue that's partly brand text in whatever
    language "Subtitlarr" happens to be surrounded by — a plausible source
    of false mismatches for weaker models — plus it isn't representative
    of the actual translated dialogue being audited anyway. Matched by
    content rather than by assuming cue #1, so this also still works for
    files written under an older disclaimer wording, and doesn't
    accidentally eat real dialogue for anyone with add_ai_disclaimer off.

    Also skips fansub-style staff-credits blocks (confirmed live: "One
    Outs" S01E19 opens with ~24 cues of "Subtitle Script Timers" /
    "Translation Consultant" / "Visual Typesettings" etc., each name/role
    a separate cue) — those are release-group credits baked into the
    SOURCE file, always in English by convention regardless of target
    language, and translating a person's name or a job title is
    meaningless anyway. They have a reliable structural signature real
    dialogue doesn't: several cues (2 or more) sharing the EXACT same
    start/end timestamp, shown as simultaneous on-screen overlay lines
    rather than one-after-another spoken dialogue. Cues are grouped by
    identical timing first; any group of 2+ is skipped entirely (both/all
    cues in it), since checking only "does this cue match the PREVIOUS
    one" would still let the first cue of each cluster through (it only
    matches the cue AFTER it). Groups of exactly 1 — the normal case for
    real dialogue — are sampled as before.

    Also samples only from the middle third of the episode's cues
    (confirmed live: "Big Windup!" S01E02 has its OP song lyrics — left
    in romanized Japanese by design, a karaoke convention, not a
    translation failure — as cues 2-17, right after the disclaimer;
    ED songs, "next episode" previews, and credits rolls similarly
    cluster at the very start/end). Sampling the middle avoids all of
    these categories at once without needing to special-case each one.

    The starting point within that middle third is randomized (jittered
    around the true midpoint), not a fixed offset — confirmed live: a
    fixed offset means the SAME sample gets pulled every single time a
    given file is checked, so a retry after a false-positive reset (e.g.
    "Kizumonogatari Part 3" — its middle-third window happened to open on
    a repeated on-screen caption) would deterministically hit the exact
    same imperfect stretch again and again, forever. A random start
    within the window gives a retry a genuine chance at a cleaner sample
    instead of being stuck on one fixed neighborhood of the file."""
    blocks = full_text.replace("\r\n", "\n").split("\n\n")
    cues: list[tuple[tuple[str, str] | None, str]] = []
    for block in blocks:
        block_lines = [l for l in block.split("\n") if l.strip()]
        if len(block_lines) < 2:
            continue
        m = _TIMESTAMP_RE.match(block_lines[1])
        timing = (m.group(1), m.group(2)) if m else None
        content = " ".join(block_lines[2:]).strip()
        cues.append((timing, content))

    scan_start, scan_end = 0, len(cues)
    if len(cues) >= MIDDLE_THIRD_MIN_CUES:
        window_start, window_end = len(cues) // 3, 2 * len(cues) // 3
        scan_start = random.randint(window_start, max(window_start, window_end - SAMPLE_CUE_COUNT))
        scan_end = window_end

    lines: list[str] = []
    i = scan_start
    while i < scan_end and len(lines) < SAMPLE_CUE_COUNT:
        timing, content = cues[i]
        group_size = 1
        if timing is not None:
            j = i + 1
            while j < len(cues) and cues[j][0] == timing:
                group_size += 1
                j += 1
        if group_size > 1:
            i += group_size
            continue
        i += 1
        if "subtitlarr" in content.lower():
            continue
        if len(content.split()) < MIN_SAMPLE_LINE_WORDS:
            continue
        # Confirmed live: a repeated on-screen caption (a character's
        # Japanese name shown as a recurring text overlay, "Hanekawa
        # Tsubasa" x8) filled an ENTIRE sample with the same line —
        # each occurrence had different timing, so the duplicate-TIMING
        # cluster filter above didn't catch it, and the check flagged a
        # genuinely, entirely correct Italian translation as Japanese
        # purely because its sample was one repeated proper noun with no
        # real dialogue signal at all. Skipping a line identical to the
        # one immediately before it in the SAMPLE (not just adjacent in
        # the file) prevents any single repeated line from dominating —
        # real dialogue essentially never repeats verbatim back-to-back
        # in a sample this sparse, so this doesn't cost genuine variety.
        if lines and content == lines[-1]:
            continue
        lines.append(content)
    return " / ".join(lines)


def _build_prompt(entries: list[dict]) -> str:
    """entries: [{"n": 1, "target_lang_name": "Catalan", "sample": "..."}]
    — the batched "what language is each of these" prompt. Numbered
    plainly (not the subtitle-specific <index> format) since this has
    nothing to do with cue reconciliation; the response format is chosen
    to be trivial to parse back (see _RESULT_LINE_RE)."""
    lines = [
        "You will be shown short text samples from several different files, "
        "each numbered. For EACH numbered sample, identify what language "
        "the text is ACTUALLY written in — ignore what language it was "
        "supposed to be translated into; just identify the real language "
        "of the text shown.",
        "Respond with EXACTLY one line per sample, in the format "
        "'<number>: <language name in English>' (e.g. '3: French'), one "
        "per line, in the same order, with no other text before, after, "
        "or between the lines.",
        "",
    ]
    for entry in entries:
        lines.append(f"Sample {entry['n']} (expected to be {entry['target_lang_name']}):")
        lines.append(entry["sample"])
        lines.append("")
    return "\n".join(lines)


def _parse_response(response: str, count: int) -> dict[int, str]:
    """{sample_number: detected_language_name}. A number missing from the
    response (model skipped/merged a line) simply isn't in the returned
    dict — the caller leaves that item 'unchecked' rather than guessing."""
    result: dict[int, str] = {}
    for line in response.splitlines():
        m = _RESULT_LINE_RE.match(line)
        if not m:
            continue
        n = int(m.group(1))
        if 1 <= n <= count:
            result[n] = m.group(2).strip()
    return result


async def run_language_check(
    conn: sqlite3.Connection, client: BazarrClient, *, batch_size: int
) -> dict:
    """Pulls up to batch_size unchecked completed items, samples each
    one's actual translated text, sends ONE batched prompt asking what
    language each sample is really in, and records ok/mismatch per item
    based on whether the detected language matches target_language.
    Returns {"checked": N, "matched": N, "mismatched": N, "skipped": N}
    — skipped covers items whose translated text couldn't be found
    (removed from Bazarr/queue since) or whose result line the response
    didn't include. An item whose text WAS found but had no dialogue
    left to sample (e.g. a file containing only the AI-disclaimer cue)
    is auto-marked 'ok' and counted under matched/checked, not skipped
    — there's no real content to ever be wrong-language, and leaving it
    'unchecked' would just re-select it on every future sweep forever."""
    with state.db_lock:
        instance_id = repository.get_config(conn, "language_check_instance_id", default=None)
    if instance_id is None:
        raise LanguageCheckError(
            "No engine selected for the language check — pick one on the Settings page first."
        )
    with state.db_lock:
        instance = engine_instances_repo.get_instance(conn, instance_id)
    if instance is None:
        raise LanguageCheckError("The engine selected for the language check no longer exists.")

    with state.db_lock:
        items = repository.get_items_for_language_check(conn, batch_size)
    if not items:
        return {"checked": 0, "matched": 0, "mismatched": 0, "skipped": 0}

    entries = []
    entry_items = []
    skipped = 0
    auto_ok = 0
    for item in items:
        text = await _get_translated_text(conn, client, item)
        if not text:
            # Text genuinely couldn't be fetched (Bazarr/NAS unreachable,
            # item removed since) — leave 'unchecked' so a later sweep,
            # once the source is reachable again, gets a real chance at it.
            skipped += 1
            continue
        sample = _sample_text(text)
        if not sample:
            # Text WAS found, but nothing survived sampling (e.g. a file
            # whose only cue is the Subtitlarr disclaimer line, itself
            # filtered out — confirmed live: "Paperman", a 1-cue file).
            # There is no dialogue left to ever be wrong-language, and
            # this is permanent (not a transient fetch failure), so
            # leaving it 'unchecked' forever would just re-select it on
            # every future sweep for no reason — mark it 'ok' instead.
            with state.db_lock:
                repository.set_language_check_ok(conn, item["id"])
            auto_ok += 1
            continue
        entries.append({
            "n": len(entries) + 1,
            "target_lang_name": language_names.language_name(item["target_language"]),
            "sample": sample,
        })
        entry_items.append(item)

    if not entries:
        return {"checked": auto_ok, "matched": auto_ok, "mismatched": 0, "skipped": skipped}

    provider = registry.build_provider(
        instance["provider_type"], instance["config"], instance_name=instance["name"]
    )
    try:
        response = await provider.ask(_build_prompt(entries))
    finally:
        await provider.aclose()

    detected = _parse_response(response, len(entries))

    matched = 0
    mismatched = 0
    for entry, item in zip(entries, entry_items):
        detected_lang = detected.get(entry["n"])
        if detected_lang is None:
            skipped += 1
            continue
        expected_name = entry["target_lang_name"].lower()
        # Loose substring match, not exact equality — a model might say
        # "Catalan" vs "ca" vs "Catalan (Spain)"; expected_name is always
        # the full English name (language_name()'s output), so checking
        # it appears somewhere in the model's answer is more robust than
        # demanding an exact string match.
        is_match = expected_name in detected_lang.lower()
        if is_match:
            with state.db_lock:
                repository.set_language_check_ok(conn, item["id"])
            matched += 1
        else:
            detail = f"detected as {detected_lang}, expected {entry['target_lang_name']}"
            # Logged BEFORE the reset, as a permanent record independent
            # of the item's own lifecycle — reset_item_for_language_
            # mismatch clears items.language_check_status/detail back to
            # 'unchecked'/NULL the moment the item is requeued, so without
            # this there'd be no durable answer to "which items did we
            # already send to Bazarr with the wrong language" once the
            # item is (hopefully correctly) retranslated.
            with state.db_lock:
                repository.log_language_mismatch(
                    conn, item_id=item["id"], item_title=item["title"], item_type=item["item_type"],
                    bazarr_id=item["bazarr_id"], target_language=item["target_language"],
                    detected_language=detected_lang, was_uploaded=(item["status"] == "done"),
                    series_title=item["series_title"], season_episode=item["season_episode"],
                )
            # A confirmed mismatch is treated like a real translation
            # failure, not a passive flag — the wrong-language output is
            # discarded (queued file removed, if any) and the item goes
            # back to 'pending' for a fresh attempt, so it can never be
            # silently pushed to Bazarr just because nobody noticed a
            # flag before the next push.
            if item["status"] == "translated_pending_upload":
                (upload_queue.DEFAULT_QUEUE_ROOT / f"{item['id']}.srt").unlink(missing_ok=True)
            with state.db_lock:
                repository.reset_item_for_language_mismatch(conn, item["id"], detail)
            mismatched += 1
            logger.warning(
                "Language check: item %d (%s) expected %s, detected %s — reset to pending",
                item["id"], item["title"], entry["target_lang_name"], detected_lang,
            )

    matched += auto_ok
    return {"checked": matched + mismatched, "matched": matched, "mismatched": mismatched, "skipped": skipped}
