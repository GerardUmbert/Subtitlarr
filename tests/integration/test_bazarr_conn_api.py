import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import state
from app.config import settings
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "bazarr_base_url", "")
    monkeypatch.setattr(settings, "bazarr_api_key", "")
    with TestClient(app) as c:
        yield c


@respx.mock
def test_test_connection_uses_unsaved_form_values_without_persisting(client):
    respx.get("http://form-entered.test:6767/api/system/status").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    resp = client.post(
        "/api/config/bazarr/test",
        json={"base_url": "http://form-entered.test:6767", "api_key": "unsaved-key"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # crucially: nothing was persisted by testing
    cfg = client.get("/api/config/bazarr").json()
    assert cfg["base_url"] != "http://form-entered.test:6767"
    assert cfg["has_key"] is False


@respx.mock
def test_test_connection_falls_back_to_saved_values_when_field_blank(client):
    respx.get("http://saved.test:6767/api/system/status").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    save_resp = client.post(
        "/api/config/bazarr", json={"base_url": "http://saved.test:6767", "api_key": "savedkey"}
    )
    assert save_resp.status_code == 200

    resp = client.post("/api/config/bazarr/test", json={"base_url": None, "api_key": None})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_test_connection_no_url_configured_reports_clearly(client):
    resp = client.post("/api/config/bazarr/test", json={})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
