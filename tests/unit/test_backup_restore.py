import sqlite3
from pathlib import Path

import pytest

from app.db import database, repository
from app.engine import backup


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    conn = database.connect(path)
    database.apply_migrations(conn)
    repository.upsert_item_seen(
        conn, item_type="episode", bazarr_id=1, series_id=1,
        title="Ep", series_title="Show", season_episode="1x1", target_language="es",
    )
    conn.close()
    return path


def test_restore_recovers_a_deleted_item(db_path):
    result = backup.run_backup(db_path, keep_count=20)
    backup_filename = Path(result["path"]).name

    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM items")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0

    backup.restore_backup(conn, db_path, backup_filename)

    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_restoring_an_older_schema_backup_gets_migrated_back_to_current(tmp_path):
    """Regression test: confirmed live (v0.9.10) that restoring a backup
    taken on an OLDER release — missing a column a later migration added
    (purge_exempt, migration 0017) — left the live database on the OLD
    schema until the next restart, so the very next call to a function
    that touches the new column (e.g. purge_unsynced_items) would crash.
    restore_backup's caller (the /backups/restore endpoint) must
    re-apply migrations immediately, synchronously, so there's no
    dangerous window at all regardless of how much schema has changed
    since the backup was taken."""
    # Build an "old" backup by creating a DB and stopping it BEFORE the
    # migration that adds purge_exempt, simulating a backup taken on an
    # older release.
    old_path = str(tmp_path / "old.db")
    old_conn = sqlite3.connect(old_path)
    from app.db.database import MIGRATIONS_DIR
    old_conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
    for migration_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = int(migration_file.name.split("_", 1)[0])
        if version >= 17:  # stop before purge_exempt
            continue
        sql = migration_file.read_text(encoding="utf-8")
        old_conn.executescript(sql)
        old_conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    old_conn.commit()
    cols_before = [r[1] for r in old_conn.execute("PRAGMA table_info(items)").fetchall()]
    assert "purge_exempt" not in cols_before
    old_conn.close()

    # "Live" DB is on the CURRENT schema.
    live_path = str(tmp_path / "live.db")
    live_conn = database.connect(live_path)
    database.apply_migrations(live_conn)

    # Restore the OLD-schema backup into the live connection, exactly
    # like the /backups/restore endpoint does, followed immediately by
    # a re-migration — this is the fix under test.
    source = sqlite3.connect(old_path)
    try:
        source.backup(live_conn)
    finally:
        source.close()
    database.apply_migrations(live_conn)

    cols_after = [r[1] for r in live_conn.execute("PRAGMA table_info(items)").fetchall()]
    assert "purge_exempt" in cols_after  # schema brought current, no restart needed

    # And the endpoint this mirrors must not crash calling a function
    # that touches the new column right after — the exact live failure.
    repository.purge_unsynced_items(live_conn, still_wanted=set())
