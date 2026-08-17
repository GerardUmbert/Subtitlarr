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


def test_list_job_events_merges_in_scheduled_translation_runs(conn):
    """job_events itself never gets a 'translate' row written to it (that
    would be a second writer for what run_history already owns durably) —
    list_job_events merges scheduled runs in read-only instead, so the
    Jobs page can show translation activity alongside the other cron jobs
    without a duplicate write path."""
    with conn:
        conn.execute(
            "INSERT INTO job_events (job, triggered_by, started_at, finished_at, status, result) "
            "VALUES ('sync_media', 'cron', '2026-08-17T01:00:00', '2026-08-17T01:00:05', 'done', '5 seen')"
        )
    run_id = repository.start_run(conn, "scheduled")
    repository.finish_run(conn, run_id, processed=3, failed=1)

    events = repository.list_job_events(conn)

    jobs = {e["job"] for e in events}
    assert "sync_media" in jobs
    assert "translate" in jobs
    translate_event = next(e for e in events if e["job"] == "translate")
    assert translate_event["status"] == "done"
    assert translate_event["result"] == "3 processed, 1 failed"
    assert translate_event["triggered_by"] == "cron"


def test_purge_unsynced_items_removes_everything_but_translated(conn):
    repository.upsert_item_seen(
        conn, item_type="episode", bazarr_id=99, series_id=2,
        title="Ep", series_title="Show", season_episode="1x1",
        target_language="es",
    )
    repository.upsert_item_seen(
        conn, item_type="episode", bazarr_id=100, series_id=2,
        title="Ep2", series_title="Show", season_episode="1x2",
        target_language="es",
    )
    done_item = conn.execute("SELECT id FROM items WHERE bazarr_id = 100").fetchone()
    repository.update_item_status(conn, done_item["id"], "done", mark_completed=True)

    purged = repository.purge_unsynced_items(conn, still_wanted=set())

    assert purged == 1
    remaining = conn.execute("SELECT bazarr_id, status FROM items").fetchall()
    assert [dict(r) for r in remaining] == [{"bazarr_id": 100, "status": "done"}]


def test_purge_unsynced_items_spares_language_check_reset(conn):
    """Regression test: confirmed live (v0.9.9) that a language-check
    mismatch reset set an item back to 'pending' specifically so the NEXT
    translation run would retry it — but the very next Bazarr poll
    deleted it outright (purge_unsynced_items treats any 'pending' item
    as safe to wipe and rebuild from Bazarr's current wanted list). Bazarr
    never re-reports a mismatch-flagged item as "missing" (the
    wrong-language file is still sitting in that slot as far as Bazarr's
    concerned), so once purged it could never be rediscovered — three
    real flagged items vanished entirely, gone from the Queue AND from a
    fresh Bazarr pull. reset_item_for_language_mismatch must mark the
    item purge_exempt so it survives until it's actually retranslated."""
    repository.upsert_item_seen(
        conn, item_type="episode", bazarr_id=99, series_id=2,
        title="Ep", series_title="Show", season_episode="1x1",
        target_language="es",
    )
    item = conn.execute("SELECT id FROM items WHERE bazarr_id = 99").fetchone()
    repository.update_item_status(conn, item["id"], "done", mark_completed=True)

    repository.reset_item_for_language_mismatch(conn, item["id"], "detected as English")
    row = conn.execute("SELECT status, purge_exempt FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert row["status"] == "pending"
    assert row["purge_exempt"] == 1

    purged = repository.purge_unsynced_items(conn, still_wanted=set())

    assert purged == 0
    survivor = conn.execute("SELECT bazarr_id FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert survivor is not None  # still here — this is the exact bug that made it vanish

    # Once genuinely retranslated, the exemption lifts — a LATER reset to
    # pending (e.g. a stuck-translating cleanup) would purge normally again.
    repository.update_item_status(conn, item["id"], "done", mark_completed=True)
    row = conn.execute("SELECT purge_exempt FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert row["purge_exempt"] == 0


def test_purge_unsynced_items_spares_still_wanted_pending_item(conn):
    """Regression test: purge_unsynced_items used to wipe EVERY non-done
    item unconditionally, including ones Bazarr still currently reports as
    wanted — poll_once then reinserted them as brand-new rows, resetting
    first_seen_wanted to "now" on every single poll. Since nothing else
    ever ages first_seen_wanted, an item could never accumulate enough age
    to cross an age_threshold_days > 0 gate — the scheduled run's queue was
    silently empty every day for over a week in production. A still-wanted
    pending item must now survive the purge with its original
    first_seen_wanted untouched."""
    repository.upsert_item_seen(
        conn, item_type="episode", bazarr_id=99, series_id=2,
        title="Ep", series_title="Show", season_episode="1x1",
        target_language="es",
    )
    item = conn.execute("SELECT id, first_seen_wanted FROM items WHERE bazarr_id = 99").fetchone()
    original_first_seen = item["first_seen_wanted"]

    purged = repository.purge_unsynced_items(
        conn, still_wanted={("episode", 99, "es")}
    )

    assert purged == 0
    survivor = conn.execute(
        "SELECT id, first_seen_wanted FROM items WHERE bazarr_id = 99"
    ).fetchone()
    assert survivor is not None
    assert survivor["id"] == item["id"]  # same row, not deleted-and-reinserted
    assert survivor["first_seen_wanted"] == original_first_seen


def test_purge_unsynced_items_deletes_orphaned_run_log(conn):
    repository.upsert_item_seen(
        conn, item_type="episode", bazarr_id=99, series_id=2,
        title="Ep", series_title="Show", season_episode="1x1",
        target_language="es",
    )
    # left as 'pending' (upsert_item_seen's default) — not purge_exempt,
    # so it's actually eligible for this purge, unlike a 'failed' item
    pending_item = conn.execute("SELECT id FROM items WHERE bazarr_id = 99").fetchone()
    repository.log_item_attempt(conn, pending_item["id"], None, "failed")

    repository.purge_unsynced_items(conn, still_wanted=set())

    assert conn.execute("SELECT COUNT(*) FROM item_run_log").fetchone()[0] == 0


def test_purge_unsynced_items_spares_failed_items(conn):
    """Regression test: confirmed live that a 'failed' item (e.g. an
    engine hitting its rate/quota limit, or any other translation
    failure) got wiped by the very next sync_media poll the same way
    language-check/stale-audit resets did before purge_exempt existed for
    them (see test_purge_unsynced_items_spares_language_check_reset).
    Bazarr never re-reports a failed item as newly "missing", so once
    purged it silently vanished from the Queue with no record of the
    failure, and would only reappear by being rediscovered and
    reprocessed from scratch. update_item_status must mark 'failed' items
    purge_exempt so they survive until they actually succeed."""
    repository.upsert_item_seen(
        conn, item_type="episode", bazarr_id=99, series_id=2,
        title="Ep", series_title="Show", season_episode="1x1",
        target_language="es",
    )
    item = conn.execute("SELECT id FROM items WHERE bazarr_id = 99").fetchone()

    repository.update_item_status(conn, item["id"], "failed", error_message="quota exceeded")
    row = conn.execute("SELECT status, purge_exempt FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert row["status"] == "failed"
    assert row["purge_exempt"] == 1

    purged = repository.purge_unsynced_items(conn, still_wanted=set())

    assert purged == 0
    survivor = conn.execute("SELECT bazarr_id FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert survivor is not None  # still here — this is the exact bug that made it vanish

    # Once genuinely retranslated, the exemption lifts — a LATER failure
    # would purge normally again unless the same protection re-applies.
    repository.update_item_status(conn, item["id"], "done", mark_completed=True)
    row = conn.execute("SELECT purge_exempt FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert row["purge_exempt"] == 0


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
