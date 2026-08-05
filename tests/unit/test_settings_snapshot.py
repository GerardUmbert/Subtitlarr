import json

import pytest

from app.db import database, repository


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


def test_log_item_attempt_stores_settings_snapshot(conn):
    """Regression test: diagnosing the batch-size regression required
    cross-referencing timestamps against server restarts to guess which
    config was live for a given attempt, since nothing recorded it directly.
    Each attempt must store exactly what config produced its result."""
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=1, series_id=None,
        title="Fastball", series_title=None, season_episode=None,
        target_language="it",
    )
    item = conn.execute("SELECT id FROM items WHERE bazarr_id = 1").fetchone()

    snapshot = {
        "engine": "ollama",
        "num_ctx": 8192,
        "batch_token_budget_override": 900,
        "resolved_batch_token_budget": 900,
    }
    repository.log_item_attempt(
        conn, item["id"], run_id=None, status="done",
        engine_used="ollama", settings_snapshot=snapshot,
    )

    row = conn.execute(
        "SELECT settings_snapshot FROM item_run_log WHERE item_id = ?", (item["id"],)
    ).fetchone()
    assert json.loads(row["settings_snapshot"]) == snapshot


def test_log_item_attempt_snapshot_is_optional(conn):
    """Existing call sites without a snapshot must keep working (NULL, not
    an error) — settings_snapshot is an added diagnostic, not a required
    field for every log entry."""
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=1, series_id=None,
        title="Fastball", series_title=None, season_episode=None,
        target_language="it",
    )
    item = conn.execute("SELECT id FROM items WHERE bazarr_id = 1").fetchone()

    repository.log_item_attempt(conn, item["id"], run_id=None, status="done")

    row = conn.execute(
        "SELECT settings_snapshot FROM item_run_log WHERE item_id = ?", (item["id"],)
    ).fetchone()
    assert row["settings_snapshot"] is None
