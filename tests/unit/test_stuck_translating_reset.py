import pytest

from app.db import database, repository


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


def test_reset_stuck_translating_items(conn):
    """Regression test: an item left in 'translating' by a process that
    died mid-batch (e.g. server restart) must not stay stuck forever — it
    should reset to 'pending' on the next startup so it gets retried."""
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=1, series_id=None,
        title="Superfast!", series_title=None, season_episode=None, target_language="it",
    )
    item = conn.execute("SELECT id FROM items WHERE bazarr_id = 1").fetchone()
    repository.update_item_status(conn, item["id"], "translating", mark_attempt=True)

    row = conn.execute("SELECT * FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert row["status"] == "translating"

    reset_count = repository.reset_stuck_translating_items(conn)
    assert reset_count == 1

    row = conn.execute("SELECT * FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert row["status"] == "pending"
    assert "restart" in row["error_message"].lower()


def test_reset_leaves_other_statuses_untouched(conn):
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=1, series_id=None,
        title="A", series_title=None, season_episode=None, target_language="it",
    )
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=2, series_id=None,
        title="B", series_title=None, season_episode=None, target_language="it",
    )
    item1 = conn.execute("SELECT id FROM items WHERE bazarr_id = 1").fetchone()
    item2 = conn.execute("SELECT id FROM items WHERE bazarr_id = 2").fetchone()
    repository.update_item_status(conn, item1["id"], "done", mark_completed=True)
    repository.update_item_status(conn, item2["id"], "failed", error_message="boom")

    reset_count = repository.reset_stuck_translating_items(conn)
    assert reset_count == 0

    row1 = conn.execute("SELECT * FROM items WHERE id = ?", (item1["id"],)).fetchone()
    row2 = conn.execute("SELECT * FROM items WHERE id = ?", (item2["id"],)).fetchone()
    assert row1["status"] == "done"
    assert row2["status"] == "failed"
    assert row2["error_message"] == "boom"


def test_reset_returns_zero_when_nothing_stuck(conn):
    assert repository.reset_stuck_translating_items(conn) == 0
