import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import database, repository
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    # Seed the DB file directly, before the app's lifespan opens its own
    # connection — TestClient runs the app in a separate thread, and
    # sqlite3 connections can't cross threads, so we can't reach into
    # state.db_conn from here once the app is up.
    seed_conn = database.connect(db_path)
    database.apply_migrations(seed_conn)
    repository.upsert_item_seen(
        seed_conn, item_type="movie", bazarr_id=1, series_id=None,
        title="Fastball", series_title=None, season_episode=None,
        target_language="it",
    )
    seed_conn.close()

    monkeypatch.setattr(settings, "db_path", db_path)
    monkeypatch.setattr(settings, "bazarr_base_url", "http://bazarr.test:6767")
    monkeypatch.setattr(settings, "bazarr_api_key", "testkey")
    with TestClient(app) as c:
        yield c


def test_run_item_reresolves_source_language_fresh(client, monkeypatch):
    """Regression test: re-running an item must check Bazarr for the
    CURRENT source language, not trust whatever was last recorded — a
    manual re-run is often prompted by exactly the fact that something
    changed on Bazarr's end (a new subtitle added/removed) since the last
    poll or attempt."""
    from app.api import queue as queue_module
    from app.engine.runner import RunController

    item = client.get("/api/queue").json()["data"][0]
    # Simulate stale cached data: item's recorded source_language is None,
    # but Bazarr NOW actually has a French subtitle available.
    assert item["source_language"] is None

    async def fake_build_source_map(client_, item_type, bazarr_id):
        return {"fr": queue_module.selector.SourceCandidate(path="/x.fr.srt", hi=False)}

    monkeypatch.setattr(queue_module.selector, "build_source_map", fake_build_source_map)

    async def fake_run_single_item(self, item_id, force_translate=False):
        return None

    monkeypatch.setattr(RunController, "run_single_item", fake_run_single_item)

    resp = client.post(f"/api/queue/{item['id']}/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["started"] is True
    assert body["source_language"] == "fr"


def test_run_item_force_query_param_reaches_run_single_item(client, monkeypatch):
    """?force=true on the run endpoint must reach run_single_item's
    force_translate — the manual "translate anyway" override for an item
    marked done via the pre-existing-subtitle skip (source_is_external),
    where the file sitting on Bazarr was never actually verified/
    translated by Subtitlarr and needs to be overwritten deliberately."""
    import threading

    from app.api import queue as queue_module
    from app.engine.runner import RunController

    item = client.get("/api/queue").json()["data"][0]

    async def fake_build_source_map(client_, item_type, bazarr_id):
        return {}

    monkeypatch.setattr(queue_module.selector, "build_source_map", fake_build_source_map)

    received = {}
    # run_single_item is fired via state.spawn_background_task, so the
    # endpoint returns 200 as soon as the task is SCHEDULED, not once it
    # has actually run — asserting on `received` right after the request
    # races the background task's own event loop (TestClient runs the app
    # in a separate thread, so there's no task handle this test can await
    # directly). A threading.Event, set from inside the faked coroutine,
    # gives a real synchronization point instead of relying on timing —
    # confirmed live as the cause of this test's intermittent CI failures
    # (KeyError: 'force_translate' when the task hadn't run yet).
    done = threading.Event()

    async def fake_run_single_item(self, item_id, force_translate=False):
        received["force_translate"] = force_translate
        done.set()
        return None

    monkeypatch.setattr(RunController, "run_single_item", fake_run_single_item)

    resp = client.post(f"/api/queue/{item['id']}/run?force=true")
    assert resp.status_code == 200
    assert done.wait(timeout=5), "run_single_item was never called"
    assert received["force_translate"] is True
