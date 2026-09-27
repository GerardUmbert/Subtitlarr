import pytest

from app.db import database, repository


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


def test_closes_job_events_with_null_finished_at(conn):
    stale_event = repository.start_job_event(conn, "disclaimer_backfill", "manual")
    # never call finish_job_event() — simulates a process killed mid-run

    closed = repository.close_stale_job_events(conn)

    assert closed == 1
    row = conn.execute(
        "SELECT finished_at, status, error FROM job_events WHERE id = ?", (stale_event,)
    ).fetchone()
    assert row["finished_at"] is not None
    assert row["status"] == "failed"
    assert row["error"]


def test_leaves_properly_finished_job_events_alone(conn):
    finished_event = repository.start_job_event(conn, "push_uploads", "cron")
    repository.finish_job_event(conn, finished_event, status="done", result="1 pushed")
    original = conn.execute(
        "SELECT finished_at FROM job_events WHERE id = ?", (finished_event,)
    ).fetchone()["finished_at"]

    closed = repository.close_stale_job_events(conn)

    assert closed == 0
    row = conn.execute("SELECT finished_at, status FROM job_events WHERE id = ?", (finished_event,)).fetchone()
    assert row["finished_at"] == original  # untouched
    assert row["status"] == "done"


def test_returns_zero_when_no_stale_job_events_exist(conn):
    assert repository.close_stale_job_events(conn) == 0
