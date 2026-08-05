import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import database, repository
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    seed_conn = database.connect(db_path)
    database.apply_migrations(seed_conn)
    for i, item_type in enumerate(["movie", "movie", "episode"]):
        repository.upsert_item_seen(
            seed_conn, item_type=item_type, bazarr_id=i, series_id=None,
            title=f"Item {i}", series_title=None, season_episode=None,
            target_language="it",
        )
    # one item already done — must not count as translatable
    done_item = seed_conn.execute("SELECT id FROM items WHERE bazarr_id = 0").fetchone()
    repository.update_item_status(seed_conn, done_item["id"], "done", mark_completed=True)
    seed_conn.close()

    monkeypatch.setattr(settings, "db_path", db_path)
    monkeypatch.setattr(settings, "bazarr_base_url", "http://bazarr.test:6767")
    monkeypatch.setattr(settings, "bazarr_api_key", "testkey")
    with TestClient(app) as c:
        yield c


def test_matching_count_excludes_non_translatable_items(client):
    resp = client.get("/api/queue/matching-count")
    assert resp.status_code == 200
    assert resp.json()["count"] == 2  # 3 seeded, 1 already 'done'


def test_matching_count_respects_item_type_filter(client):
    resp = client.get("/api/queue/matching-count", params={"item_type": "episode"})
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


def test_run_filtered_starts_a_run(client, monkeypatch):
    from app.engine.runner import RunController

    captured = {}

    async def fake_run_filtered(self, status, item_type, search):
        captured["args"] = (status, item_type, search)

    monkeypatch.setattr(RunController, "run_filtered", fake_run_filtered)

    resp = client.post("/api/queue/run-filtered", params={"item_type": "movie"})
    assert resp.status_code == 200
    assert resp.json()["started"] is True


def test_run_filtered_refuses_when_a_run_is_already_active(client):
    from app import state
    from app.engine.runner import RunProgress

    state.run_controller.current = RunProgress(active=True)
    resp = client.post("/api/queue/run-filtered")
    assert resp.status_code == 200
    body = resp.json()
    assert body["started"] is False
    assert "already in progress" in body["reason"]

    state.run_controller.current = None  # cleanup
