import pytest

from app.db import database, repository


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


def _seed_item(conn, bazarr_id: int, title: str) -> int:
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=bazarr_id, series_id=None,
        title=title, series_title=None, season_episode=None, target_language="it",
    )
    return conn.execute("SELECT id FROM items WHERE bazarr_id = ?", (bazarr_id,)).fetchone()["id"]


def test_list_run_history_returns_newest_first(conn):
    run1 = repository.start_run(conn, "manual_full")
    repository.finish_run(conn, run1, processed=1, failed=0)
    run2 = repository.start_run(conn, "scheduled")
    repository.finish_run(conn, run2, processed=2, failed=1)

    runs, total = repository.list_run_history(conn)

    assert total == 2
    assert [r["id"] for r in runs] == [run2, run1]


def test_list_run_history_paginates(conn):
    for _ in range(5):
        run_id = repository.start_run(conn, "manual_full")
        repository.finish_run(conn, run_id, processed=1, failed=0)

    runs, total = repository.list_run_history(conn, page=1, page_size=2)
    assert total == 5
    assert len(runs) == 2

    runs_page2, _ = repository.list_run_history(conn, page=2, page_size=2)
    assert len(runs_page2) == 2
    assert runs[0]["id"] != runs_page2[0]["id"]


def test_list_run_history_derives_primary_engine_from_items(conn):
    """run_history itself stores no engine — it's derived from how many
    items in that run used each engine, since a run can mix engines via
    fallback or a settings change mid-run."""
    item1 = _seed_item(conn, 1, "Movie 1")
    item2 = _seed_item(conn, 2, "Movie 2")
    item3 = _seed_item(conn, 3, "Movie 3")
    run_id = repository.start_run(conn, "manual_full")
    repository.log_item_attempt(conn, item1, run_id, "done", engine_used="nvidia")
    repository.log_item_attempt(conn, item2, run_id, "done", engine_used="nvidia")
    repository.log_item_attempt(conn, item3, run_id, "done", engine_used="gemini")
    repository.finish_run(conn, run_id, processed=3, failed=0)

    runs, _ = repository.list_run_history(conn)
    run = runs[0]

    assert run["primary_engine"] == "nvidia"
    assert run["other_engines"] == ["gemini"]


def test_list_run_history_single_engine_has_no_other_engines(conn):
    item1 = _seed_item(conn, 1, "Movie 1")
    run_id = repository.start_run(conn, "manual_full")
    repository.log_item_attempt(conn, item1, run_id, "done", engine_used="ollama")
    repository.finish_run(conn, run_id, processed=1, failed=0)

    runs, _ = repository.list_run_history(conn)
    assert runs[0]["primary_engine"] == "ollama"
    assert runs[0]["other_engines"] == []


def test_list_run_history_handles_run_with_no_logged_items(conn):
    """A run that failed before any item was even attempted (or an
    in-progress run) must still show up, just with no engine."""
    run_id = repository.start_run(conn, "manual_full")

    runs, total = repository.list_run_history(conn)
    assert total == 1
    assert runs[0]["primary_engine"] is None
    assert runs[0]["other_engines"] == []


def test_get_run_items_returns_items_for_that_run_only(conn):
    item1 = _seed_item(conn, 1, "Movie 1")
    item2 = _seed_item(conn, 2, "Movie 2")
    run1 = repository.start_run(conn, "manual_full")
    repository.log_item_attempt(conn, item1, run1, "done", engine_used="nvidia")
    run2 = repository.start_run(conn, "manual_full")
    repository.log_item_attempt(conn, item2, run2, "done", engine_used="nvidia")

    items_run1 = repository.get_run_items(conn, run1)
    items_run2 = repository.get_run_items(conn, run2)

    assert [i["item_id"] for i in items_run1] == [item1]
    assert [i["item_id"] for i in items_run2] == [item2]


def test_get_run_items_includes_current_item_fields_and_log_fields(conn):
    item1 = _seed_item(conn, 1, "Movie 1")
    run_id = repository.start_run(conn, "manual_full")
    repository.log_item_attempt(
        conn, item1, run_id, "failed", engine_used="nvidia", error_message="boom"
    )

    items = repository.get_run_items(conn, run_id)
    assert len(items) == 1
    row = items[0]
    assert row["title"] == "Movie 1"
    assert row["status"] == "failed"
    assert row["engine_used"] == "nvidia"
    assert row["error_message"] == "boom"


def test_get_run_items_empty_for_unknown_run(conn):
    assert repository.get_run_items(conn, 999) == []
