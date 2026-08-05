import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "bazarr_base_url", "http://bazarr.test:6767")
    monkeypatch.setattr(settings, "bazarr_api_key", "testkey")
    with TestClient(app) as c:
        yield c


def test_poll_endpoint_starts_and_reports_status(client, monkeypatch):
    async def fake_poll(self):
        return {"episodes_seen": 0, "movies_seen": 0}

    from app.engine.runner import RunController

    monkeypatch.setattr(RunController, "poll", fake_poll)

    resp = client.post("/api/run/poll")
    assert resp.status_code == 200
    assert resp.json()["started"] is True

    status = client.get("/api/run/poll/status").json()
    assert status["active"] is False
    assert status["error"] is None
