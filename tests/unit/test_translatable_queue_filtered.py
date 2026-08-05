import pytest

from app.db import database, repository


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


def _seed(conn, bazarr_id, item_type, title, status=None):
    repository.upsert_item_seen(
        conn, item_type=item_type, bazarr_id=bazarr_id, series_id=None,
        title=title, series_title=None, season_episode=None, target_language="it",
    )
    if status:
        item = conn.execute("SELECT id FROM items WHERE bazarr_id = ?", (bazarr_id,)).fetchone()
        repository.update_item_status(conn, item["id"], status, mark_completed=(status == "done"))


def test_filtered_queue_only_includes_translatable_statuses(conn):
    _seed(conn, 1, "movie", "A")  # pending
    _seed(conn, 2, "movie", "B", status="done")
    _seed(conn, 3, "movie", "C", status="failed")

    result = repository.get_translatable_queue_filtered(conn)
    assert [r["bazarr_id"] for r in result] == [1]


def test_filtered_queue_respects_item_type(conn):
    _seed(conn, 1, "movie", "A")
    _seed(conn, 2, "episode", "B")

    result = repository.get_translatable_queue_filtered(conn, item_type="episode")
    assert [r["bazarr_id"] for r in result] == [2]


def test_filtered_queue_respects_search(conn):
    _seed(conn, 1, "movie", "Fastball")
    _seed(conn, 2, "movie", "Superfast")

    result = repository.get_translatable_queue_filtered(conn, search="fast")
    assert {r["bazarr_id"] for r in result} == {1, 2}

    result = repository.get_translatable_queue_filtered(conn, search="ball")
    assert [r["bazarr_id"] for r in result] == [1]


def test_filtered_queue_status_filter_that_is_not_translatable_yields_nothing(conn):
    """Filtering by e.g. status='failed' correctly returns nothing, since
    the function always restricts to pending/queued regardless."""
    _seed(conn, 1, "movie", "A", status="failed")

    result = repository.get_translatable_queue_filtered(conn, status="failed")
    assert result == []
