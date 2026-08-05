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

    async def fake_run_single_item(self, item_id):
        return None

    monkeypatch.setattr(RunController, "run_single_item", fake_run_single_item)

    resp = client.post(f"/api/queue/{item['id']}/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["started"] is True
    assert body["source_language"] == "fr"
