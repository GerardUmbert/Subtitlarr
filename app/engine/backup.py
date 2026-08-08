"""Daily snapshot of the SQLite database to a non-volatile backup folder,
independent of the container filesystem — a clear-database click, a bad
migration, or a corrupted write is otherwise unrecoverable (confirmed
live: a clear-database call has no undo, see app.db.repository.clear_
queue_data). Keeps a fixed number of most-recent snapshots, oldest
pruned automatically, so this can run unattended forever without
growing without bound.
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BACKUP_DIRNAME = "backups"
BACKUP_FILENAME_PREFIX = "subtitlarr-"
DEFAULT_KEEP_COUNT = 20


def _backup_dir(db_path: str) -> Path:
    return Path(db_path).parent / BACKUP_DIRNAME


def run_backup(db_path: str, *, keep_count: int = DEFAULT_KEEP_COUNT) -> dict:
    """Uses sqlite3's own online backup API (not a raw file copy) so a
    concurrent writer on the live connection can't produce a torn/corrupt
    snapshot — safe to run while the app is serving requests or mid-run.
    Returns {"path": str, "pruned": int}."""
    backup_dir = _backup_dir(db_path)
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dest_path = backup_dir / f"{BACKUP_FILENAME_PREFIX}{timestamp}.db"

    source = sqlite3.connect(db_path)
    dest = sqlite3.connect(str(dest_path))
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()

    pruned = _prune_old_backups(backup_dir, keep_count)
    logger.info("Database backup written to %s (pruned %d old snapshot(s))", dest_path, pruned)
    return {"path": str(dest_path), "pruned": pruned}


def _prune_old_backups(backup_dir: Path, keep_count: int) -> int:
    snapshots = sorted(
        backup_dir.glob(f"{BACKUP_FILENAME_PREFIX}*.db"),
        key=lambda p: p.name,
        reverse=True,
    )
    stale = snapshots[keep_count:]
    for path in stale:
        path.unlink(missing_ok=True)
    return len(stale)
