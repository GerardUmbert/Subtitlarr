import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    with TestClient(app) as c:
        yield c


# ---------- Ollama-only utility endpoints (app/api/engines.py) ----------
# These are no longer tied to any saved engine config — base_url is a
# required param, since the UI always has a concrete instance's form value
# to send (see app/api/engines.py's module docstring for why the old
# "falls back to saved settings" behavior doesn't apply anymore).


@respx.mock
def test_list_ollama_models_uses_given_base_url(client):
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


def test_list_ollama_models_requires_base_url(client):
    resp = client.get("/api/config/engines/ollama/models")
    assert resp.status_code == 422  # FastAPI's required-query-param rejection


def test_list_ollama_models_returns_502_when_server_unreachable(client):
    resp = client.get(
        "/api/config/engines/ollama/models",
        params={"base_url": "http://nonexistent.invalid:11434"},
    )
    assert resp.status_code == 502


@respx.mock
def test_pull_uses_given_base_url(client):
    respx.post("http://form-entered.test:11434/api/pull").mock(
        return_value=httpx.Response(200, content=b'{"status":"success"}\n')
    )
    resp = client.post(
        "/api/config/engines/ollama/pull",
        json={"base_url": "http://form-entered.test:11434", "model": "gemma3:4b"},
    )
    assert resp.status_code == 200
    assert resp.json()["started"] is True


# ---------- Engine instances CRUD/test (app/api/engine_instances.py) ----------


def test_create_list_update_delete_instance_round_trip(client):
    created = client.post(
        "/api/config/engine-instances",
        json={"name": "Gemini (main)", "provider_type": "gemini", "config": {"api_key": "secret123"}},
    ).json()
    assert created["name"] == "Gemini (main)"
    assert created["provider_type"] == "gemini"
    # never echo the real key back
    assert created["config"]["api_key"] is None
    assert created["config"]["has_api_key"] is True
    assert created["config"]["api_key_masked"]

    listed = client.get("/api/config/engine-instances").json()["data"]
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]

    updated = client.put(
        f"/api/config/engine-instances/{created['id']}",
        json={"name": "Gemini (renamed)", "enabled": False},
    ).json()
    assert updated["name"] == "Gemini (renamed)"
    assert updated["enabled"] is False
    # config untouched by a request that didn't include it
    assert updated["config"]["has_api_key"] is True

    resp = client.delete(f"/api/config/engine-instances/{created['id']}")
    assert resp.json()["deleted"] is True
    assert client.get("/api/config/engine-instances").json()["data"] == []


def test_update_config_merges_not_replaces(client):
    """A blank/omitted API key field in the update request must mean 'keep
    the existing key', never 'clear it' — the same convention the old
    single-engine save endpoint used."""
    created = client.post(
        "/api/config/engine-instances",
        json={"name": "NVIDIA", "provider_type": "nvidia", "config": {"api_key": "secret123", "model": "m1"}},
    ).json()

    updated = client.put(
        f"/api/config/engine-instances/{created['id']}",
        json={"config": {"model": "m2"}},  # no api_key field at all
    ).json()
    assert updated["config"]["model"] == "m2"
    assert updated["config"]["has_api_key"] is True  # key preserved


def test_create_defaults_fill_in_missing_config_fields(client):
    created = client.post(
        "/api/config/engine-instances",
        json={"name": "NVIDIA", "provider_type": "nvidia", "config": {}},
    ).json()
    assert created["config"]["model"] == "deepseek-ai/deepseek-v4-flash"
    assert created["config"]["batch_token_budget"] == 700


def test_create_rejects_unknown_provider_type(client):
    resp = client.post(
        "/api/config/engine-instances",
        json={"name": "Bogus", "provider_type": "not-a-real-provider", "config": {}},
    )
    assert resp.status_code == 422


def test_create_separator(client):
    created = client.post(
        "/api/config/engine-instances",
        json={"name": "stop here", "provider_type": "separator"},
    ).json()
    assert created["provider_type"] == "separator"


def test_reorder_instances(client):
    first = client.post(
        "/api/config/engine-instances",
        json={"name": "A", "provider_type": "ollama", "config": {}},
    ).json()
    second = client.post(
        "/api/config/engine-instances",
        json={"name": "B", "provider_type": "ollama", "config": {}},
    ).json()

    resp = client.post(
        "/api/config/engine-instances/reorder", json={"ids": [second["id"], first["id"]]}
    )
    ordered = resp.json()["data"]
    assert [i["id"] for i in ordered] == [second["id"], first["id"]]


@respx.mock
def test_test_instance_uses_unsaved_form_values_not_saved_config(client):
    """Regression test carried over from the old single-engine API: Test
    Connection must use whatever is currently in the form, not whatever
    was last saved."""
    created = client.post(
        "/api/config/engine-instances",
        json={"name": "Ollama", "provider_type": "ollama", "config": {"base_url": "http://stale-saved.test:11434", "model": "stale-model"}},
    ).json()

    respx.get("http://form-entered.test:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "qwen2.5-coder:3b"}]})
    )
    resp = client.post(
        f"/api/config/engine-instances/{created['id']}/test",
        json={"config": {"base_url": "http://form-entered.test:11434", "model": "qwen2.5-coder:3b"}},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # confirms the saved config was untouched by the test call
    fetched = client.get("/api/config/engine-instances").json()["data"][0]
    assert fetched["config"]["base_url"] == "http://stale-saved.test:11434"


def test_test_instance_404_for_unknown_id(client):
    resp = client.post("/api/config/engine-instances/999/test")
    assert resp.status_code == 404


def test_test_separator_rejected(client):
    created = client.post(
        "/api/config/engine-instances",
        json={"name": "stop here", "provider_type": "separator"},
    ).json()
    resp = client.post(f"/api/config/engine-instances/{created['id']}/test")
    assert resp.status_code == 400
