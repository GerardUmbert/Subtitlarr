import pytest

from app.db import database, repository


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


def _seed(conn):
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=1, series_id=None,
        title="Zebra Movie", series_title=None, season_episode=None, target_language="es",
    )
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=2, series_id=None,
        title="Apple Movie", series_title=None, season_episode=None, target_language="es",
    )
    repository.upsert_item_seen(
        conn, item_type="episode", bazarr_id=3, series_id=10,
        title="Pilot", series_title="Middle Show", season_episode="1x1", target_language="es",
    )


def test_default_sort_is_alphabetical_by_title_not_recency(conn):
    """Regression test: clicking 'run' on an item bumps its last_updated,
    which used to reorder the whole table to put that row first — jarring
    and made the queue hard to scan. Default sort must be stable/alphabetical."""
    _seed(conn)

    # bump bazarr_id=1 (Zebra Movie)'s last_updated to be the most recent —
    # under the old recency sort this would jump it to the top
    item = conn.execute("SELECT id FROM items WHERE bazarr_id = 1").fetchone()
    repository.update_item_status(conn, item["id"], "translating", mark_attempt=True)

    rows, total = repository.list_queue(conn)
    titles = [r["series_title"] or r["title"] for r in rows]
    assert titles == sorted(titles, key=str.lower)
    assert titles[0] == "Apple Movie"  # NOT Zebra Movie, despite being just updated


def test_recent_sort_still_available_for_dashboard(conn):
    _seed(conn)
    item = conn.execute("SELECT id FROM items WHERE bazarr_id = 1").fetchone()
    repository.update_item_status(conn, item["id"], "translating", mark_attempt=True)

    rows, total = repository.list_queue(conn, sort="recent")
    assert (rows[0]["series_title"] or rows[0]["title"]) == "Zebra Movie"


def test_item_type_filter(conn):
    _seed(conn)
    rows, total = repository.list_queue(conn, item_type="movie")
    assert total == 2
    assert all(r["item_type"] == "movie" for r in rows)

    rows, total = repository.list_queue(conn, item_type="episode")
    assert total == 1
    assert rows[0]["series_title"] == "Middle Show"


def test_search_by_title(conn):
    _seed(conn)
    rows, total = repository.list_queue(conn, search="zebra")
    assert total == 1
    assert rows[0]["title"] == "Zebra Movie"

    rows, total = repository.list_queue(conn, search="middle")
    assert total == 1
    assert rows[0]["series_title"] == "Middle Show"

    rows, total = repository.list_queue(conn, search="nonexistent")
    assert total == 0


def test_search_escapes_like_wildcards(conn):
    """A literal % or _ in a search term must be treated literally, not as
    a SQL LIKE wildcard."""
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=1, series_id=None,
        title="100% Real Movie", series_title=None, season_episode=None, target_language="es",
    )
    rows, total = repository.list_queue(conn, search="100%")
    assert total == 1
    rows, total = repository.list_queue(conn, search="100X")
    assert total == 0


def test_filters_combine(conn):
    _seed(conn)
    rows, total = repository.list_queue(conn, item_type="movie", search="apple")
    assert total == 1
    assert rows[0]["title"] == "Apple Movie"


def test_exclude_no_source_hides_skipped_no_source_items(conn):
    """A standalone toggle, independent of status/type/search — hides
    skipped_no_source items regardless of what else is filtered."""
    _seed(conn)
    no_source_item = conn.execute("SELECT id FROM items WHERE bazarr_id = 1").fetchone()
    repository.update_item_status(conn, no_source_item["id"], "skipped_no_source")

    rows, total = repository.list_queue(conn)
    assert total == 3  # default: no exclusion, everything shows

    rows, total = repository.list_queue(conn, exclude_no_source=True)
    assert total == 2
    assert all(r["status"] != "skipped_no_source" for r in rows)


def test_exclude_no_source_stacks_with_other_filters(conn):
    _seed(conn)
    no_source_item = conn.execute("SELECT id FROM items WHERE bazarr_id = 2").fetchone()
    repository.update_item_status(conn, no_source_item["id"], "skipped_no_source")

    # both movies match item_type=movie; excluding no-source should drop
    # the skipped one, leaving just the other movie
    rows, total = repository.list_queue(conn, item_type="movie", exclude_no_source=True)
    assert total == 1
    assert rows[0]["title"] == "Zebra Movie"


def test_exclude_no_source_ignored_when_explicitly_filtering_to_that_status(conn):
    """Explicitly selecting the 'No source' status filter must still show
    those items even if the exclude toggle is somehow also on — an
    explicit status filter always wins."""
    _seed(conn)
    no_source_item = conn.execute("SELECT id FROM items WHERE bazarr_id = 1").fetchone()
    repository.update_item_status(conn, no_source_item["id"], "skipped_no_source")

    rows, total = repository.list_queue(
        conn, status="skipped_no_source", exclude_no_source=True
    )
    assert total == 1
