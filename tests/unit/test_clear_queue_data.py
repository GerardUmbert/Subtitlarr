import pytest

from app.db import database, repository


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


def test_clear_queue_data_wipes_items_runs_and_logs_but_keeps_config(conn):
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=1, series_id=None,
        title="Fastball", series_title=None, season_episode=None,
        target_language="it",
    )
    item = conn.execute("SELECT id FROM items WHERE bazarr_id = 1").fetchone()
    run_id = repository.start_run(conn, "manual_full")
    repository.log_item_attempt(conn, item["id"], run_id, "done")
    repository.set_config(conn, "source_lang_priority", ["en", "it"])

    result = repository.clear_queue_data(conn)

    assert result == {"items_cleared": 1, "runs_cleared": 1, "logs_cleared": 1}
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM run_history").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM item_run_log").fetchone()[0] == 0
    assert repository.get_config(conn, "source_lang_priority") == ["en", "it"]


def test_clear_queue_data_resets_autoincrement_counters(conn):
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=1, series_id=None,
        title="A", series_title=None, season_episode=None, target_language="it",
    )
    repository.clear_queue_data(conn)

    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=2, series_id=None,
        title="B", series_title=None, season_episode=None, target_language="it",
    )
    new_item = conn.execute("SELECT id FROM items WHERE bazarr_id = 2").fetchone()
    assert new_item["id"] == 1  # counter reset, not continuing from a higher value


def test_clear_queue_data_on_empty_db_is_a_noop(conn):
    result = repository.clear_queue_data(conn)
    assert result == {"items_cleared": 0, "runs_cleared": 0, "logs_cleared": 0}
