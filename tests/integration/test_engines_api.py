import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "ollama_base_url", "http://stale-saved.test:11434")
    monkeypatch.setattr(settings, "ollama_model", "stale-model")
    with TestClient(app) as c:
        yield c


@respx.mock
def test_test_ollama_uses_unsaved_form_values_not_saved_settings(client):
    """Regression test: Test Connection must use whatever is currently in
    the form, not whatever was last saved — this was the bug where testing
    an unsaved IP appeared to fail because it silently tested stale config."""
    respx.get("http://form-entered.test:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen2.5-coder:3b"}]})
    )
    resp = client.post(
        "/api/config/engines/ollama/test",
        json={"base_url": "http://form-entered.test:11434", "model": "qwen2.5-coder:3b"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True

    # confirms it did NOT hit the stale saved URL
    cfg = client.get("/api/config/engines").json()
    assert cfg["ollama_base_url"] == "http://stale-saved.test:11434"


@respx.mock
def test_test_ollama_reports_model_not_found_clearly(client):
    respx.get("http://form-entered.test:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen2.5-coder:3b"}]})
    )
    resp = client.post(
        "/api/config/engines/ollama/test",
        json={"base_url": "http://form-entered.test:11434", "model": "gemma3:4b"},
    )
    body = resp.json()
    assert body["ok"] is False
    assert "gemma3:4b" in body["detail"]
    assert "qwen2.5-coder:3b" in body["detail"]


@respx.mock
def test_pull_uses_unsaved_form_values_not_saved_settings(client):
    """Regression test: Pull Model must target the form's current base_url,
    not require Save to have succeeded first."""
    respx.post("http://form-entered.test:11434/api/pull").mock(
        return_value=httpx.Response(200, content=b'{"status":"success"}\n')
    )
    resp = client.post(
        "/api/config/engines/ollama/pull",
        json={"base_url": "http://form-entered.test:11434", "model": "gemma3:4b"},
    )
    assert resp.status_code == 200
    assert resp.json()["started"] is True


@respx.mock
def test_list_ollama_models_uses_unsaved_base_url(client):
    """Regression pattern from the test/pull endpoints above: switching the
    base_url field must list models from THAT server, not the saved one."""
    respx.get("http://form-entered.test:11434/api/tags").mock(
        return_value=httpx.Response(
            200,
            json={
                "models": [
                    {"name": "gemma3:4b", "size": 100, "details": {"parameter_size": "4.3B", "quantization_level": "Q4_K_M"}},
                    {"name": "gemma3:12b", "size": 200, "details": {"parameter_size": "12.2B", "quantization_level": "Q4_K_M"}},
                ]
            },
        )
    )
    resp = client.get(
        "/api/config/engines/ollama/models",
        params={"base_url": "http://form-entered.test:11434"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["models"]) == 2
    assert body["models"][0]["name"] == "gemma3:4b"
    assert body["models"][1]["name"] == "gemma3:12b"


@respx.mock
def test_list_ollama_models_falls_back_to_saved_base_url_when_not_provided(client):
    respx.get("http://stale-saved.test:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": []})
    )
    resp = client.get("/api/config/engines/ollama/models")
    assert resp.status_code == 200
    assert resp.json()["models"] == []


def test_list_ollama_models_returns_502_when_server_unreachable(client):
    resp = client.get(
        "/api/config/engines/ollama/models",
        params={"base_url": "http://nonexistent.invalid:11434"},
    )
    assert resp.status_code == 502
