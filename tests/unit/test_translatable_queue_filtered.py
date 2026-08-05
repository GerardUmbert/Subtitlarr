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
    """With no status filter, 'done' is excluded (avoid silently
    re-translating already-completed items on a bare bulk-run click) but
    'failed' IS included — a live case surfaced 2 failed + 1 pending items
    visible together on the 'All' tab, and the bulk-run button only picked
    up the 1 pending, silently skipping the 2 failed rows shown right in
    the same table."""
    _seed(conn, 1, "movie", "A")  # pending
    _seed(conn, 2, "movie", "B", status="done")
    _seed(conn, 3, "movie", "C", status="failed")

    result = repository.get_translatable_queue_filtered(conn)
    assert {r["bazarr_id"] for r in result} == {1, 3}


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


def test_filtered_queue_with_no_status_filter_excludes_done_but_includes_failed(conn):
    """With NO status filter ('All' tab), the passive default excludes
    'done' — bulk-running 'everything' shouldn't silently re-translate
    already-completed items — but DOES include 'failed', since those are
    visibly broken/incomplete rows sitting in the same 'All' table, not
    something a user would expect a bulk-run click to skip over."""
    _seed(conn, 1, "movie", "A")  # pending
    _seed(conn, 2, "movie", "B", status="done")
    _seed(conn, 3, "movie", "C", status="failed")

    result = repository.get_translatable_queue_filtered(conn)
    assert {r["bazarr_id"] for r in result} == {1, 3}


def test_filtered_queue_with_explicit_failed_filter_returns_failed_items(conn):
    """Regression test: filtering to 'Failed' on the Queue page and hitting
    'Run all N matching' must actually retry those items — an explicit
    status filter is clearly an intent to act on that status, not something
    that should be silently overridden by the passive pending/queued
    default."""
    _seed(conn, 1, "movie", "A")  # pending
    _seed(conn, 2, "movie", "B", status="failed")
    _seed(conn, 3, "movie", "C", status="failed")

    result = repository.get_translatable_queue_filtered(conn, status="failed")
    assert {r["bazarr_id"] for r in result} == {2, 3}


def test_filtered_queue_with_explicit_done_filter_returns_done_items(conn):
    """Same guarantee for 'Done' — the Queue page's own copy says any row,
    including done ones, can be re-run."""
    _seed(conn, 1, "movie", "A", status="done")

    result = repository.get_translatable_queue_filtered(conn, status="done")
    assert [r["bazarr_id"] for r in result] == [1]


def test_filtered_queue_with_translating_status_filter_yields_nothing(conn):
    """The one genuinely non-rerunnable status — an item already mid-
    translation must never be picked up by a bulk run."""
    _seed(conn, 1, "movie", "A", status="translating")

    result = repository.get_translatable_queue_filtered(conn, status="translating")
    assert result == []
