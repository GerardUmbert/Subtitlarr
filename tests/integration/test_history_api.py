import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import database, repository
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "bazarr_base_url", "http://bazarr.test:6767")
    monkeypatch.setattr(settings, "bazarr_api_key", "testkey")
    with TestClient(app) as c:
        yield c


def _seed_conn():
    return database.connect(settings.db_path)


def test_get_history_returns_empty_when_no_runs(client):
    resp = client.get("/api/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["total"] == 0


def test_get_history_lists_runs_newest_first(client):
    conn = _seed_conn()
    run1 = repository.start_run(conn, "manual_full")
    repository.finish_run(conn, run1, processed=1, failed=0)
    run2 = repository.start_run(conn, "scheduled")
    repository.finish_run(conn, run2, processed=2, failed=0)
    conn.close()

    resp = client.get("/api/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert [r["id"] for r in body["data"]] == [run2, run1]


def test_get_history_run_items_returns_logged_items(client):
    conn = _seed_conn()
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=1, series_id=None,
        title="Fastball", series_title=None, season_episode=None, target_language="it",
    )
    item = conn.execute("SELECT id FROM items WHERE bazarr_id = 1").fetchone()
    run_id = repository.start_run(conn, "manual_full")
    repository.log_item_attempt(conn, item["id"], run_id, "done", engine_used="nvidia")
    conn.close()

    resp = client.get(f"/api/history/{run_id}/items")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["title"] == "Fastball"
    assert body["data"][0]["engine_used"] == "nvidia"


def test_get_history_run_items_404_for_unknown_run(client):
    resp = client.get("/api/history/999/items")
    assert resp.status_code == 404
