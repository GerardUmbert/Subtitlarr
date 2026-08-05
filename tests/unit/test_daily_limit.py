import pytest

from app.db import database, repository


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


def _seed_done_item(conn, bazarr_id: int):
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=bazarr_id, series_id=None,
        title=f"Movie {bazarr_id}", series_title=None, season_episode=None,
        target_language="it",
    )
    item = conn.execute(
        "SELECT id FROM items WHERE bazarr_id = ?", (bazarr_id,)
    ).fetchone()
    repository.update_item_status(conn, item["id"], "done", mark_completed=True)
    return item["id"]


def test_count_completed_today_counts_only_done_items(conn):
    _seed_done_item(conn, 1)
    _seed_done_item(conn, 2)
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=3, series_id=None,
        title="Failed one", series_title=None, season_episode=None,
        target_language="it",
    )
    failed_item = conn.execute("SELECT id FROM items WHERE bazarr_id = 3").fetchone()
    repository.update_item_status(conn, failed_item["id"], "failed", error_message="boom")

    assert repository.count_completed_today(conn) == 2


def test_count_completed_today_ignores_items_completed_before_today(conn):
    item_id = _seed_done_item(conn, 1)
    # Backdate completed_at to yesterday to prove the UTC-midnight cutoff works.
    with conn:
        conn.execute(
            "UPDATE items SET completed_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
            (item_id,),
        )

    assert repository.count_completed_today(conn) == 0


def test_count_completed_today_zero_when_nothing_done(conn):
    assert repository.count_completed_today(conn) == 0
