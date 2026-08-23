"""One-time (per item), opt-in backfill: stamps the model name onto the
AI disclaimer line of an already-translated item's subtitle, matching
what new translations get automatically since v0.13.0 (see
srt_io.disclaimer_text's model_name param). Manual/opt-in, not run on
any schedule — this is a real Bazarr write (re-upload) for every
eligible item, not a read.
"""

import logging
import sqlite3

from app import state
from app.bazarr.client import BazarrClient
from app.db import repository
from app.engine import upload_queue
from app.subtitles import srt_io

logger = logging.getLogger(__name__)


def _find_disclaimer_index(subs: list) -> int | None:
    """The disclaimer cue is always prepended first by with_ai_disclaimer,
    but matched by content (contains "subtitlarr", case-insensitive) —
    same marker used everywhere else in the codebase to identify a
    Subtitlarr-authored cue (see translator.py's is_own_prior_upload,
    language_check.py's _sample_text) — rather than assuming index 0,
    in case a file's cues were ever reordered."""
    for i, sub in enumerate(subs):
        if "subtitlarr" in (sub.content or "").lower():
            return i
    return None


async def backfill_item(
    conn: sqlite3.Connection, client: BazarrClient, item: sqlite3.Row,
) -> str:
    """Returns one of: "tagged", "skipped_no_disclaimer",
    "skipped_no_content". Never raises for an expected per-item miss —
    those are reported back for the caller to tally, same pattern as
    language_check.run_language_check's skipped count, so one item's
    quirk doesn't abort the whole backfill pass."""
    item_id = item["id"]
    model_name = item["model_used"]

    if item["status"] == "translated_pending_upload":
        path = upload_queue.DEFAULT_QUEUE_ROOT / f"{item_id}.srt"
        if not path.exists():
            return "skipped_no_content"
        subs = srt_io.parse_srt_bytes(path.read_bytes())
    else:
        if item["item_type"] == "episode":
            detail = await client.get_episode_detail(item["bazarr_id"])
        else:
            detail = await client.get_movie_detail(item["bazarr_id"])
        if detail is None:
            return "skipped_no_content"
        match = next(
            (s for s in detail.subtitles if s.code2 == item["target_language"] and s.path and not s.forced),
            None,
        )
        if match is None:
            return "skipped_no_content"
        cues = await client.get_subtitle_contents(match.path)
        if not cues:
            return "skipped_no_content"
        subs = srt_io.cues_from_bazarr(cues)

    disclaimer_idx = _find_disclaimer_index(subs)
    if disclaimer_idx is None:
        # Not actually Subtitlarr's own output despite source_is_external=0
        # (e.g. add_ai_disclaimer was off for this translation) — nothing
        # to edit. Marked tagged anyway so this item stops being
        # re-selected by every future backfill pass for no reason.
        with state.db_lock:
            repository.mark_disclaimer_model_tagged(conn, item_id)
        return "skipped_no_disclaimer"

    if f"[{model_name}]" in subs[disclaimer_idx].content:
        # Already carries this exact tag (e.g. a previous backfill run
        # got interrupted after uploading but before marking tagged) —
        # avoid double-appending "[model] [model]" on a re-run.
        with state.db_lock:
            repository.mark_disclaimer_model_tagged(conn, item_id)
        return "tagged"

    subs[disclaimer_idx].content = f"{subs[disclaimer_idx].content} [{model_name}]"
    srt_bytes = srt_io.compose_srt(subs)

    if item["status"] == "translated_pending_upload":
        upload_queue.save_pending_upload(upload_queue.DEFAULT_QUEUE_ROOT, item_id, srt_bytes)
    elif item["item_type"] == "episode":
        await client.upload_episode_subtitle(
            series_id=item["series_id"], episode_id=item["bazarr_id"],
            language_code2=item["target_language"], srt_bytes=srt_bytes,
        )
    else:
        await client.upload_movie_subtitle(
            radarr_id=item["bazarr_id"], language_code2=item["target_language"], srt_bytes=srt_bytes,
        )

    with state.db_lock:
        repository.mark_disclaimer_model_tagged(conn, item_id)
    return "tagged"


async def run_disclaimer_model_backfill(conn: sqlite3.Connection, client: BazarrClient) -> dict:
    """Runs the backfill across every eligible item in one pass. One
    item's failure (Bazarr unreachable, upload error, etc.) is logged and
    counted but doesn't abort the rest — same reasoning as
    upload_queue.push_pending_uploads."""
    with state.db_lock:
        items = repository.get_items_for_disclaimer_model_backfill(conn)

    tagged = 0
    skipped_no_disclaimer = 0
    skipped_no_content = 0
    errored = 0
    for item in items:
        try:
            outcome = await backfill_item(conn, client, item)
        except Exception:  # noqa: BLE001 - one item's failure must not abort the batch
            errored += 1
            logger.exception("Disclaimer model backfill failed for item %s", item["id"])
            continue
        if outcome == "tagged":
            tagged += 1
        elif outcome == "skipped_no_disclaimer":
            skipped_no_disclaimer += 1
        else:
            skipped_no_content += 1

    return {
        "total": len(items),
        "tagged": tagged,
        "skipped_no_disclaimer": skipped_no_disclaimer,
        "skipped_no_content": skipped_no_content,
        "errored": errored,
    }
