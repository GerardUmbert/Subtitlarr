import time

import pytest

from app.db import database, repository


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


def test_migrations_apply_and_are_idempotent(conn):
    database.apply_migrations(conn)  # second call should no-op, not error
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"items", "run_history", "item_run_log", "app_config", "schema_version"} <= tables


def test_upsert_item_seen_stamps_first_seen_once(conn):
    repository.upsert_item_seen(
        conn,
        item_type="episode",
        bazarr_id=42,
        series_id=1,
        title="Legacy",
        series_title="The Bear",
        season_episode="3x7",
        target_language="es",
    )
    row = conn.execute("SELECT * FROM items WHERE bazarr_id = 42").fetchone()
    first_seen = row["first_seen_wanted"]
    assert row["status"] == "pending"

    time.sleep(0.01)
    # second poll observing the same item should NOT move first_seen_wanted
    repository.upsert_item_seen(
        conn,
        item_type="episode",
        bazarr_id=42,
        series_id=1,
        title="Legacy",
        series_title="The Bear",
        season_episode="3x7",
        target_language="es",
    )
    row2 = conn.execute("SELECT * FROM items WHERE bazarr_id = 42").fetchone()
    assert row2["first_seen_wanted"] == first_seen
    assert row2["last_updated"] != row["last_updated"]


def test_age_gated_queue_respects_threshold(conn):
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=1, series_id=None,
        title="Perfect Days", series_title=None, season_episode=None,
        target_language="en",
    )
    # fresh item: not yet old enough for a 14-day gate
    assert repository.get_age_gated_queue(conn, age_threshold_days=14) == []
    # but always visible in the full/manual queue
    assert len(repository.get_full_translatable_queue(conn)) == 1

    # backdate first_seen_wanted to simulate an aged item
    with conn:
        conn.execute(
            "UPDATE items SET first_seen_wanted = datetime('now', '-20 days') WHERE bazarr_id = 1"
        )
    assert len(repository.get_age_gated_queue(conn, age_threshold_days=14)) == 1


def test_mark_resolved_if_missing_clears_item_no_longer_wanted(conn):
    repository.upsert_item_seen(
        conn, item_type="episode", bazarr_id=99, series_id=2,
        title="Ep", series_title="Show", season_episode="1x1",
        target_language="es",
    )
    repository.mark_resolved_if_missing(conn, "episode", 99, still_missing_languages=set())
    row = conn.execute("SELECT * FROM items WHERE bazarr_id = 99").fetchone()
    assert row["status"] == "done"


def test_stats_counts_by_status(conn):
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=1, series_id=None,
        title="A", series_title=None, season_episode=None, target_language="en",
    )
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=2, series_id=None,
        title="B", series_title=None, season_episode=None, target_language="en",
    )
    item2 = conn.execute("SELECT id FROM items WHERE bazarr_id = 2").fetchone()
    repository.mark_skipped_no_source(conn, item2["id"])

    stats = repository.get_stats(conn)
    assert stats["wanted"] == 2
    assert stats["translatable"] == 1
    assert stats["no_source"] == 1


def test_app_config_roundtrip(conn):
    repository.set_config(conn, "source_lang_priority", ["en", "it", "fr"])
    assert repository.get_config(conn, "source_lang_priority") == ["en", "it", "fr"]
    assert repository.get_config(conn, "missing_key", default="fallback") == "fallback"


def test_retrying_a_failed_item_clears_the_stale_error(conn):
    """Regression test: a failed attempt's error_message must not survive
    into a subsequent retry or success — otherwise a 'done' item can still
    show a leftover error from an earlier failed attempt, which is
    confusing and was observed live."""
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=1, series_id=None,
        title="A", series_title=None, season_episode=None, target_language="it",
    )
    item = conn.execute("SELECT id FROM items WHERE bazarr_id = 1").fetchone()

    repository.update_item_status(conn, item["id"], "failed", error_message="boom")
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert row["error_message"] == "boom"

    # a fresh attempt (retry) must clear the old error immediately
    repository.update_item_status(conn, item["id"], "translating", mark_attempt=True)
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert row["error_message"] is None

    # and success must never leave a stale error visible either
    repository.update_item_status(conn, item["id"], "failed", error_message="boom again")
    repository.update_item_status(
        conn, item["id"], "done", source_language="en", engine_used="ollama", mark_completed=True
    )
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert row["status"] == "done"
    assert row["error_message"] is None
