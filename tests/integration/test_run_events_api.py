import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.engine import run_events
from app.main import app


@pytest.fixture(autouse=True)
def clear_events():
    run_events._events.clear()
    yield
    run_events._events.clear()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "bazarr_base_url", "http://bazarr.test:6767")
    monkeypatch.setattr(settings, "bazarr_api_key", "testkey")
    with TestClient(app) as c:
        yield c


def test_get_run_events_returns_empty_with_no_events(client):
    resp = client.get("/api/run/events")
    assert resp.status_code == 200
    assert resp.json() == {"events": []}


def test_get_run_events_returns_emitted_events(client):
    run_events.emit(1, 100, 2, 5, "retrying", "nvidia: 504 — retrying in 62s")

    resp = client.get("/api/run/events")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["events"]) == 1
    event = body["events"][0]
    assert event["run_id"] == 1
    assert event["item_id"] == 100
    assert event["batch_index"] == 2
    assert event["batch_total"] == 5
    assert event["event_type"] == "retrying"
    assert "504" in event["detail"]


def test_get_run_events_respects_since_param(client):
    run_events.emit(1, 100, 1, 2, "retrying", "first")
    first_id = client.get("/api/run/events").json()["events"][0]["id"]
    run_events.emit(1, 100, 2, 2, "retry_succeeded", "second")

    resp = client.get(f"/api/run/events?since={first_id}")
    body = resp.json()
    assert len(body["events"]) == 1
    assert body["events"][0]["detail"] == "second"


def test_get_latest_event_id_returns_zero_with_no_events(client):
    resp = client.get("/api/run/events/latest_id")
    assert resp.status_code == 200
    assert resp.json() == {"id": 0}


def test_get_latest_event_id_matches_most_recently_emitted(client):
    run_events.emit(1, 100, 1, 2, "retrying", "first")
    run_events.emit(1, 100, 2, 2, "retry_succeeded", "second")
    latest = client.get("/api/run/events").json()["events"][-1]["id"]

    resp = client.get("/api/run/events/latest_id")
    assert resp.json() == {"id": latest}

    # Seeking to it and polling with `since` set to that id must return
    # nothing further — this is what lets a freshly loaded page skip the
    # whole buffered backlog instead of replaying it as toasts.
    resp = client.get(f"/api/run/events?since={latest}")
    assert resp.json() == {"events": []}
