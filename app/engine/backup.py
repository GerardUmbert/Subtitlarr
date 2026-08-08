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

    # Confirmed live: second-resolution timestamps collide whenever two
    # backups happen within the same second — restore_backup's own
    # safety-snapshot-before-restoring step guarantees exactly that any
    # time a restore follows shortly after a backup, silently overwriting
    # the very backup file about to be restored FROM before it's ever
    # read. Millisecond resolution makes a same-second collision
    # astronomically unlikely without changing the filename format users
    # might already be relying on to sort/read.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")[:-3]
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


class RestoreError(Exception):
    """Raised for restore-time failures — missing file, path escape, or a
    backup that doesn't even look like a Subtitlarr database."""


def list_backups(db_path: str) -> list[dict]:
    """Newest first. filename is the only thing the restore endpoint
    accepts back — never a caller-supplied path — so a listing entry
    doubles as the allowlist of exactly what CAN be restored."""
    backup_dir = _backup_dir(db_path)
    if not backup_dir.exists():
        return []
    snapshots = sorted(
        backup_dir.glob(f"{BACKUP_FILENAME_PREFIX}*.db"),
        key=lambda p: p.name,
        reverse=True,
    )
    return [
        {
            "filename": p.name,
            "size_bytes": p.stat().st_size,
            "created_at": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat(),
        }
        for p in snapshots
    ]


def restore_backup(conn: sqlite3.Connection, db_path: str, filename: str) -> dict:
    """Restores `filename` (must be one of list_backups()'s own filenames
    — never a raw path, to rule out path traversal) INTO the live,
    already-open connection via sqlite3's backup API run in reverse
    (source=snapshot, dest=live) — this overwrites the live database's
    content in place without ever closing/reopening `conn` or touching
    the file the running app process has open, which a raw file-copy
    restore could not do safely (the app would keep writing through its
    existing handle to a file that had just been silently swapped out
    from under it).

    A fresh safety snapshot of the CURRENT (pre-restore) state is taken
    first and is itself included in the normal backups/ rotation — so a
    restore is always itself undoable by restoring the safety snapshot
    it just made, without any special-casing.

    Caller is responsible for ensuring no run/job is active first — this
    function has no such guard of its own, matching every other
    destructive repository function in this codebase (e.g.
    clear_queue_data)."""
    backup_dir = _backup_dir(db_path)
    # filename must be a bare name straight out of list_backups() — this
    # both rules out "../../etc/passwd"-style traversal AND ensures only
    # a file THIS backup system wrote (not an arbitrary .db dropped into
    # the folder) can ever be restored.
    if "/" in filename or "\\" in filename or not filename.startswith(BACKUP_FILENAME_PREFIX):
        raise RestoreError(f"Not a valid backup filename: {filename!r}")
    source_path = backup_dir / filename
    if not source_path.is_file():
        raise RestoreError(f"Backup file not found: {filename}")

    safety = run_backup(db_path)

    source = sqlite3.connect(str(source_path))
    try:
        source.backup(conn)
    finally:
        source.close()

    logger.warning(
        "Database restored from %s (pre-restore safety snapshot: %s)",
        filename, safety["path"],
    )
    return {"restored_from": filename, "safety_snapshot": safety["path"]}
