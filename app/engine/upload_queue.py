import logging
import sqlite3
from pathlib import Path

from app.bazarr.client import BazarrClient
from app.config import settings
from app.db import repository

logger = logging.getLogger(__name__)

# Unlike prefetch.DEFAULT_SCRATCH_ROOT (a disposable, re-fetchable source
# cache), these files ARE the finished LLM translation output — the only
# copy of it until it's pushed to Bazarr. queue_uploads_enabled exists
# specifically so a run can sit queued for a while (deliberately deferring
# the NAS wake-up), so this must survive a container restart — lives
# alongside the SQLite DB on the persistent /data volume, not the
# container's ephemeral temp filesystem.
DEFAULT_QUEUE_ROOT = Path(settings.db_path).parent / "upload-queue"


def save_pending_upload(queue_dir: Path, item_id: int, srt_bytes: bytes) -> Path:
    queue_dir.mkdir(parents=True, exist_ok=True)
    path = queue_dir / f"{item_id}.srt"
    path.write_bytes(srt_bytes)
    return path


async def push_pending_uploads(
    conn: sqlite3.Connection, client: BazarrClient, queue_dir: Path = DEFAULT_QUEUE_ROOT
) -> dict:
    """Uploads every item sitting in 'translated_pending_upload' to Bazarr
    in one pass, then marks it done. One item's upload failure doesn't
    abort the rest — it's left in place (both the DB status and the
    queued file) so a later push can retry it.

    A missing queued file (e.g. items queued before the queue dir moved to
    the persistent volume, or the file was otherwise lost) is unrecoverable
    by retrying the push — the translated content is simply gone. Those
    items are reset to 'pending' instead of left permanently stuck, so the
    next translation run regenerates them.

    A language-check mismatch (see app.engine.language_check) never
    reaches this function at all — a confirmed mismatch immediately
    discards the queued file and resets the item to 'pending' right when
    the check runs, rather than leaving a flagged item sitting here for a
    later push to skip."""
    items = repository.get_items_by_status(conn, "translated_pending_upload")

    pushed = 0
    failed = 0
    reset = 0
    for item in items:
        item_id = item["id"]
        path = queue_dir / f"{item_id}.srt"
        if not path.exists():
            logger.warning(
                "Item %d marked translated_pending_upload but no queued file at %s; "
                "resetting to pending for re-translation",
                item_id, path,
            )
            repository.update_item_status(conn, item_id, "pending")
            reset += 1
            continue
        try:
            srt_bytes = path.read_bytes()
            if item["item_type"] == "episode":
                await client.upload_episode_subtitle(
                    series_id=item["series_id"],
                    episode_id=item["bazarr_id"],
                    language_code2=item["target_language"],
                    srt_bytes=srt_bytes,
                )
            else:
                await client.upload_movie_subtitle(
                    radarr_id=item["bazarr_id"],
                    language_code2=item["target_language"],
                    srt_bytes=srt_bytes,
                )
            # completed_at was already stamped when translation finished
            # (see translator.translate_item) — pushing to Bazarr later
            # doesn't change WHEN the translation itself completed, so
            # mark_completed is deliberately omitted here to avoid
            # overwriting it with the (much later) push time.
            repository.update_item_status(conn, item_id, "done")
            path.unlink(missing_ok=True)
            pushed += 1
        except Exception:  # noqa: BLE001 - one bad upload must not abort the push
            logger.warning("Push failed for item %d; left queued for retry", item_id, exc_info=True)
            failed += 1

    return {"pushed": pushed, "failed": failed, "reset": reset}
