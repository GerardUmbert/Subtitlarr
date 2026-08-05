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


def test_closes_runs_with_null_finished_at(conn):
    stale_run = repository.start_run(conn, "manual_full")
    # never call finish_run() — simulates a process killed mid-batch

    closed = repository.close_stale_open_runs(conn)

    assert closed == 1
    row = conn.execute("SELECT finished_at FROM run_history WHERE id = ?", (stale_run,)).fetchone()
    assert row["finished_at"] is not None


def test_leaves_properly_finished_runs_alone(conn):
    finished_run = repository.start_run(conn, "manual_full")
    repository.finish_run(conn, finished_run, processed=1, failed=0)
    original = conn.execute(
        "SELECT finished_at FROM run_history WHERE id = ?", (finished_run,)
    ).fetchone()["finished_at"]

    closed = repository.close_stale_open_runs(conn)

    assert closed == 0
    row = conn.execute("SELECT finished_at FROM run_history WHERE id = ?", (finished_run,)).fetchone()
    assert row["finished_at"] == original  # untouched


def test_backfills_processed_and_failed_counts_from_item_run_log(conn):
    item1 = _seed_item(conn, 1, "Movie 1")
    item2 = _seed_item(conn, 2, "Movie 2")
    stale_run = repository.start_run(conn, "manual_full")
    repository.log_item_attempt(conn, item1, stale_run, "done", engine_used="nvidia")
    repository.log_item_attempt(conn, item2, stale_run, "failed", error_message="boom")

    repository.close_stale_open_runs(conn)

    row = conn.execute(
        "SELECT items_processed, items_failed FROM run_history WHERE id = ?", (stale_run,)
    ).fetchone()
    assert row["items_processed"] == 2
    assert row["items_failed"] == 1


def test_does_not_delete_the_run_or_its_item_history(conn):
    """Explicit distinction from clear_queue_data() — this must be
    non-destructive, per the user's explicit instruction that stale-run
    cleanup should NOT remove completed data, only fix broken open runs."""
    item1 = _seed_item(conn, 1, "Movie 1")
    stale_run = repository.start_run(conn, "manual_full")
    repository.log_item_attempt(conn, item1, stale_run, "done", engine_used="nvidia")

    repository.close_stale_open_runs(conn)

    assert conn.execute("SELECT COUNT(*) FROM run_history").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM item_run_log").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_returns_zero_when_no_stale_runs_exist(conn):
    assert repository.close_stale_open_runs(conn) == 0


def test_handles_run_with_no_logged_items_at_all(conn):
    """A run killed before it even reached its first item's log_item_attempt
    call — must still close, with zero counts, not crash on a NULL MAX()."""
    stale_run = repository.start_run(conn, "manual_full")

    closed = repository.close_stale_open_runs(conn)

    assert closed == 1
    row = conn.execute(
        "SELECT finished_at, items_processed, items_failed FROM run_history WHERE id = ?",
        (stale_run,),
    ).fetchone()
    assert row["finished_at"] is not None
    assert row["items_processed"] == 0
    assert row["items_failed"] == 0
