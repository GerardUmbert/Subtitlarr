"""One-off (or periodic) audit of every 'done' item against Bazarr's
CURRENT subtitle state — confirmed live (v0.9.7): ~170 'done' items
across 13 shows had no real subtitle file on Bazarr for their
target_language at all (path=None, meaning only an embedded/original
track exists, not something Bazarr manages as a file) despite being
marked done. Root cause understood but not fully reconstructible after
the fact (a stale wanted-list report, Bazarr later deleting/replacing
the uploaded file, etc.) — see translator.translate_item's own guard for
the forward-looking half of this fix. This module is the backward-looking
half: find every item that's currently in that same broken state and
reset it so it flows through the normal pipeline again, this time behind
the new guard.

Deliberately does NOT re-upload or otherwise touch Bazarr itself — pure
detection + a local status reset, exactly like a language-check mismatch
reset (see app.engine.language_check.run_language_check).
"""

import logging
import sqlite3

from app import state
from app.bazarr.client import BazarrClient
from app.db import repository

logger = logging.getLogger(__name__)


async def _has_real_target_subtitle(
    client: BazarrClient, item: sqlite3.Row
) -> bool | None:
    """None means "couldn't determine" (item missing from Bazarr entirely,
    e.g. deleted from Sonarr/Radarr since) — treated as inconclusive, NOT
    stale, since resetting an item Bazarr no longer even knows about would
    just leave it stranded pending forever with nothing to check against."""
    if item["item_type"] == "episode":
        detail = await client.get_episode_detail(item["bazarr_id"])
    else:
        detail = await client.get_movie_detail(item["bazarr_id"])
    if detail is None:
        return None
    return any(
        s.code2 == item["target_language"] and s.path and not s.forced
        for s in detail.subtitles
    )


async def run_stale_audit(conn: sqlite3.Connection, client: BazarrClient) -> dict:
    """Checks every 'done' item's target_language against Bazarr's
    CURRENT subtitle list. An item with no real (path'd, non-forced)
    subtitle in its target language is reset to 'pending' — Subtitlarr's
    own record says "translated," but Bazarr shows nothing there now, so
    the record is simply wrong regardless of how it got that way.
    Returns {"checked": N, "stale": N, "ok": N, "inconclusive": N}."""
    with state.db_lock:
        items = repository.get_done_items_for_stale_audit(conn)
    stale = 0
    ok = 0
    inconclusive = 0
    for item in items:
        has_real = await _has_real_target_subtitle(client, item)
        if has_real is None:
            inconclusive += 1
            continue
        if has_real:
            ok += 1
            continue
        stale += 1
        logger.warning(
            "Stale audit: item %d (%s, target=%s) has no real subtitle on "
            "Bazarr despite status='done' — resetting to pending.",
            item["id"], item["title"], item["target_language"],
        )
        with state.db_lock:
            repository.reset_item_for_stale_audit(conn, item["id"])
    return {
        "checked": len(items),
        "stale": stale,
        "ok": ok,
        "inconclusive": inconclusive,
    }
