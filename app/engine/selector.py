import logging
import sqlite3
from dataclasses import dataclass

import httpx

from app import state
from app.bazarr.client import BazarrClient, BazarrError
from app.db import repository

logger = logging.getLogger(__name__)


@dataclass
class SourceCandidate:
    path: str
    hi: bool


async def build_source_map(
    client: BazarrClient, item_type: str, bazarr_id: int
) -> dict[str, SourceCandidate]:
    """Returns {lang_code2: SourceCandidate} of subtitles Bazarr already has
    for this item — the candidate pool for translation sources. Forced
    subtitles (usually only cover foreign-language snippets, not full
    dialogue) are always excluded. HI (hearing-impaired) subtitles are kept
    but flagged, so they can be used as a last resort rather than dropped
    outright — see pick_source_language."""
    if item_type == "episode":
        detail = await client.get_episode_detail(bazarr_id)
    else:
        detail = await client.get_movie_detail(bazarr_id)
    if detail is None:
        return {}
    candidates: dict[str, SourceCandidate] = {}
    for sub in detail.subtitles:
        if not sub.path or sub.forced:
            continue
        # Only .srt is supported — app/subtitles/srt_io.py's parser
        # assumes SRT's index/timecode/text block structure and doesn't
        # attempt to handle any other format. Confirmed live: Bazarr's own
        # /api/subtitles/contents endpoint returned a 500 trying to serve
        # an .ass file's content, so this is filtered out before ever
        # being attempted as a translation source, not left to fail at
        # fetch time. Same reasoning would apply to .ssa/.vtt/.sub/etc. —
        # none of those are SRT-structured either, even if Bazarr happened
        # to serve one successfully.
        if not sub.path.lower().endswith(".srt"):
            continue
        # Prefer a non-HI track over an HI one for the same language if
        # both exist; only keep the HI one if it's all we have for that code.
        existing = candidates.get(sub.code2)
        if existing is None or (existing.hi and not sub.hi):
            candidates[sub.code2] = SourceCandidate(path=sub.path, hi=sub.hi)
    return candidates


def pick_source_language(
    source_map: dict[str, SourceCandidate], target_lang: str, source_priority: list[str]
) -> str | None:
    """Picks which existing language to translate from. The priority list is
    a PREFERENCE order, not a whitelist — an item is never skipped just
    because its only existing language wasn't explicitly configured (that
    would silently break for anyone whose library isn't English-first).

    Preference order:
      1. Priority-list language, non-HI track
      2. Priority-list language, HI track (better to translate a real
         dialogue track with bracketed sound cues than guess at a
         different language's subtitle, which may not even match)
      3. Any other available language, non-HI
      4. Any other available language, HI
    """
    candidates = [(lang, c) for lang, c in source_map.items() if lang != target_lang]

    for lang in source_priority:
        match = source_map.get(lang)
        if match and lang != target_lang and not match.hi:
            return lang
    for lang in source_priority:
        match = source_map.get(lang)
        if match and lang != target_lang and match.hi:
            return lang
    for lang, c in candidates:
        if not c.hi:
            return lang
    for lang, c in candidates:
        if c.hi:
            return lang
    return None


async def resolve_and_gate(
    conn: sqlite3.Connection,
    client: BazarrClient,
    items: list[sqlite3.Row],
    source_priority: list[str],
) -> list[dict]:
    """For each candidate item, resolves its source language/path via Bazarr.
    Items with no usable source (no existing subtitle in any language other
    than the target itself) are marked skipped_no_source and excluded.
    Returns a list of dicts ready for the translator: item row + resolved
    source_lang/source_path.

    A per-item Bazarr failure (timeout, connection error, 5xx, malformed
    response) is caught and marks JUST that one item 'failed' rather than
    aborting the whole batch — confirmed live: an unguarded call here let
    one bad Bazarr response for one item in a 5-item filtered run silently
    kill resolve_and_gate entirely, which (since this runs before
    run_batch's own try/finally) killed the whole fire-and-forget run_batch
    task with no error ever reaching the app's logs or the UI. The other
    4 healthy items in that batch never even got a chance to run."""
    ready = []
    for item in items:
        try:
            source_map = await build_source_map(client, item["item_type"], item["bazarr_id"])
        except (httpx.HTTPError, BazarrError) as exc:
            logger.error(
                "resolve_and_gate: Bazarr call failed for item %d (%s); marking failed, "
                "continuing with the rest of the batch",
                item["id"], exc,
            )
            with state.db_lock:
                repository.update_item_status(
                    conn, item["id"], "failed",
                    error_message=f"Could not resolve source from Bazarr: {exc}",
                )
            continue
        matched_lang = pick_source_language(source_map, item["target_language"], source_priority)
        if matched_lang is None:
            with state.db_lock:
                repository.mark_skipped_no_source(conn, item["id"])
            continue
        ready.append(
            {
                "item": item,
                "source_lang": matched_lang,
                "source_path": source_map[matched_lang].path,
            }
        )
    return ready


def get_age_gated_queue(conn: sqlite3.Connection, age_threshold_days: int) -> list[sqlite3.Row]:
    with state.db_lock:
        return repository.get_age_gated_queue(conn, age_threshold_days)


def get_full_translatable_queue(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    with state.db_lock:
        return repository.get_full_translatable_queue(conn)


def get_filtered_translatable_queue(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    item_type: str | None = None,
    search: str | None = None,
    model: str | None = None,
) -> list[sqlite3.Row]:
    with state.db_lock:
        return repository.get_translatable_queue_filtered(
            conn, status=status, item_type=item_type, search=search, model=model
        )
