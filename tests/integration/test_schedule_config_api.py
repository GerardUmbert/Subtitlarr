import pytest
from fastapi.testclient import TestClient

from app.config import Settings, settings
from app.main import app

from tests.integration.conftest import log_in


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "bazarr_base_url", "http://bazarr.test:6767")
    monkeypatch.setattr(settings, "bazarr_api_key", "testkey")
    with TestClient(app) as c:
        log_in(c)
        yield c


CUSTOM_PAYLOAD = {
    "cron_expression": "1 1 * * *",
    "age_threshold_days": 1,
    "daily_translation_limit": 1,
    "pause_between_items_seconds": 1,
    "clear_rate_limits_before_scheduled_run": False,
    "queue_uploads_enabled": True,
    "push_uploads_cron": "",
    "sync_media_cron": "",
    "sync_subs_cron": "",
    "language_check_cron": "",
    "backup_cron": "",
    "telemetry_enabled": False,
}


def test_set_persists_every_field(client):
    resp = client.post("/api/config/schedule", json=CUSTOM_PAYLOAD)
    assert resp.status_code == 200, resp.text

    resp = client.get("/api/config/schedule")
    assert resp.status_code == 200
    body = resp.json()
    for key, value in CUSTOM_PAYLOAD.items():
        assert body[key] == value, key


def test_set_then_reset_all(client):
    resp = client.post("/api/config/schedule", json=CUSTOM_PAYLOAD)
    assert resp.status_code == 200, resp.text

    resp = client.post("/api/config/schedule/reset", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    defaults = Settings()
    assert body["cron_expression"] == defaults.schedule_cron
    assert body["backup_cron"] == defaults.backup_cron
    assert body["sync_media_cron"] == defaults.sync_media_cron
    assert body["telemetry_enabled"] == defaults.telemetry_enabled


def test_set_then_reset_single_field(client):
    client.post("/api/config/schedule", json=CUSTOM_PAYLOAD)

    resp = client.post("/api/config/schedule/reset", json={"fields": ["backup_cron"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["backup_cron"] == Settings().backup_cron
    assert body["cron_expression"] == "1 1 * * *"
    assert body["telemetry_enabled"] is False


def test_reset_accepts_cron_expression_alias_for_translation_cron(client):
    client.post("/api/config/schedule", json=CUSTOM_PAYLOAD)

    resp = client.post("/api/config/schedule/reset", json={"fields": ["cron_expression"]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cron_expression"] == Settings().schedule_cron
    assert body["backup_cron"] == ""


def test_reset_unknown_field_rejected(client):
    resp = client.post("/api/config/schedule/reset", json={"fields": ["not_a_real_field"]})
    assert resp.status_code == 422
