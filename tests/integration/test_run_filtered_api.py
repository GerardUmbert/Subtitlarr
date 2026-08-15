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


def test_matching_count_with_explicit_done_filter_includes_done_items(client):
    """Regression test: filtering the Queue page to 'Done' and checking the
    bulk-run count must reflect actual done items, not always 0 — an
    explicit status filter is trusted, unlike the no-filter default which
    excludes done/failed."""
    resp = client.get("/api/queue/matching-count", params={"status": "done"})
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


def test_run_filtered_starts_a_run(client, monkeypatch):
    from app.engine.runner import RunController

    captured = {}

    async def fake_run_filtered(self, status, item_type, search, model=None):
        captured["args"] = (status, item_type, search, model)

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


def test_run_filtered_refuses_honestly_when_daily_limit_already_reached(client, monkeypatch):
    """Regression test: with the daily cap already exhausted, run-filtered
    previously returned {"started": True} unconditionally (the check only
    lived inside run_batch's fire-and-forget background task, which then
    silently sliced ready_items down to nothing) — the click looked
    successful but translated zero items with no error anywhere. The API
    must check the cap BEFORE scheduling the background task and refuse
    honestly, the same way it already refuses for an active run."""
    from app import state
    from app.engine.runner import RunController

    monkeypatch.setattr(RunController, "daily_limit_remaining", lambda self: 0)

    called = {"run_filtered": False}

    async def fake_run_filtered(self, status, item_type, search, model=None):
        called["run_filtered"] = True

    monkeypatch.setattr(RunController, "run_filtered", fake_run_filtered)

    resp = client.post("/api/queue/run-filtered")
    assert resp.status_code == 200
    body = resp.json()
    assert body["started"] is False
    assert "limit" in body["reason"].lower()
    assert called["run_filtered"] is False  # never even scheduled
