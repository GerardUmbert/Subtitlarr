"""HTTP-level coverage for stage 5 of AUTH_PLAN.md (local, untracked
design doc): POST /api/queue/{item_id}/manual-translation accepts a
short-lived, single-use, item-scoped upload token as a full alternative
to a real session or the MCP bearer token — see
tests/unit/test_manual_translation_upload_token.py for the underlying
repository-level consume logic."""
import pytest
from fastapi.testclient import TestClient

from app import state
from app.config import settings
from app.db import database, repository
from app.main import app

from tests.integration.conftest import log_in


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
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


def _item_id(client) -> int:
    log_in(client)
    item = client.get("/api/queue").json()["data"][0]
    client.post("/api/auth/logout")
    return item["id"]


def _stub_manual_translation(monkeypatch):
    from app.api import queue as queue_module

    async def fake_resolve_and_gate(conn, client_, items, source_priority):
        return [{"item": items[0], "source_lang": "en", "source_path": "/x.srt"}]

    async def fake_submit(conn, client_, item, source_lang, source_path, translated_text, model_name):
        return {"status": "done"}

    monkeypatch.setattr(queue_module.selector, "resolve_and_gate", fake_resolve_and_gate)
    monkeypatch.setattr(queue_module.manual_translation, "submit_manual_translation", fake_submit)


def test_submission_with_no_auth_at_all_rejected(client, monkeypatch):
    _stub_manual_translation(monkeypatch)
    item_id = _item_id(client)

    resp = client.post(
        f"/api/queue/{item_id}/manual-translation",
        json={"translated_text": "1\nHola.", "model_name": "claude-code"},
    )
    assert resp.status_code == 401


def test_valid_upload_token_authenticates_submission(client, monkeypatch):
    _stub_manual_translation(monkeypatch)
    item_id = _item_id(client)
    with state.db_lock:
        repository.create_manual_translation_upload_token(
            state.get_conn(), "tok-abc", item_id, "2099-01-01T00:00:00+00:00",
        )

    resp = client.post(
        f"/api/queue/{item_id}/manual-translation",
        json={"translated_text": "1\nHola.", "model_name": "claude-code"},
        headers={"X-Upload-Token": "tok-abc"},
    )
    assert resp.status_code == 200


def test_upload_token_cannot_be_reused(client, monkeypatch):
    _stub_manual_translation(monkeypatch)
    item_id = _item_id(client)
    with state.db_lock:
        repository.create_manual_translation_upload_token(
            state.get_conn(), "tok-abc", item_id, "2099-01-01T00:00:00+00:00",
        )

    first = client.post(
        f"/api/queue/{item_id}/manual-translation",
        json={"translated_text": "1\nHola.", "model_name": "claude-code"},
        headers={"X-Upload-Token": "tok-abc"},
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/queue/{item_id}/manual-translation",
        json={"translated_text": "1\nHola.", "model_name": "claude-code"},
        headers={"X-Upload-Token": "tok-abc"},
    )
    assert second.status_code == 401


def test_upload_token_rejected_for_a_different_item(client, monkeypatch):
    _stub_manual_translation(monkeypatch)
    item_id = _item_id(client)
    with state.db_lock:
        repository.create_manual_translation_upload_token(
            state.get_conn(), "tok-abc", item_id + 999, "2099-01-01T00:00:00+00:00",
        )

    resp = client.post(
        f"/api/queue/{item_id}/manual-translation",
        json={"translated_text": "1\nHola.", "model_name": "claude-code"},
        headers={"X-Upload-Token": "tok-abc"},
    )
    assert resp.status_code == 401


def test_expired_upload_token_rejected(client, monkeypatch):
    _stub_manual_translation(monkeypatch)
    item_id = _item_id(client)
    with state.db_lock:
        repository.create_manual_translation_upload_token(
            state.get_conn(), "tok-abc", item_id, "2000-01-01T00:00:00+00:00",
        )

    resp = client.post(
        f"/api/queue/{item_id}/manual-translation",
        json={"translated_text": "1\nHola.", "model_name": "claude-code"},
        headers={"X-Upload-Token": "tok-abc"},
    )
    assert resp.status_code == 401


def test_valid_session_still_authenticates_submission_without_any_token(client, monkeypatch):
    """The normal session/MCP-token path must keep working — the upload
    token is an ALTERNATIVE, not a replacement."""
    _stub_manual_translation(monkeypatch)
    log_in(client)
    item = client.get("/api/queue").json()["data"][0]

    resp = client.post(
        f"/api/queue/{item['id']}/manual-translation",
        json={"translated_text": "1\nHola.", "model_name": "claude-code"},
    )
    assert resp.status_code == 200


def test_getting_source_mints_a_usable_upload_token(client, monkeypatch):
    """End-to-end: the GET .../source route (what the MCP tool calls)
    mints a real token that the POST route then accepts."""
    from app.api import queue as queue_module

    async def fake_resolve_and_gate(conn, client_, items, source_priority):
        return [{"item": items[0], "source_lang": "en", "source_path": "/x.srt"}]

    async def fake_get_subtitle_contents(path):
        from app.bazarr.schemas import SubtitleCue, SubtitleCueTime
        return [
            SubtitleCue(
                index=1, content="Hello.", proprietary="",
                start=SubtitleCueTime(hours=0, minutes=0, seconds=0, total_seconds=0, microseconds=0),
                end=SubtitleCueTime(hours=0, minutes=0, seconds=1, total_seconds=1, microseconds=0),
            )
        ]

    monkeypatch.setattr(queue_module.selector, "resolve_and_gate", fake_resolve_and_gate)

    log_in(client)
    item = client.get("/api/queue").json()["data"][0]

    monkeypatch.setattr(state.get_client(), "get_subtitle_contents", fake_get_subtitle_contents)

    source_resp = client.get(f"/api/queue/{item['id']}/manual-translation/source")
    assert source_resp.status_code == 200
    upload_token = source_resp.json()["upload_token"]
    assert upload_token

    client.post("/api/auth/logout")

    async def fake_submit(conn, client_, item_, source_lang, source_path, translated_text, model_name):
        return {"status": "done"}

    monkeypatch.setattr(queue_module.manual_translation, "submit_manual_translation", fake_submit)

    submit_resp = client.post(
        f"/api/queue/{item['id']}/manual-translation",
        json={"translated_text": "1\nHola.", "model_name": "claude-code"},
        headers={"X-Upload-Token": upload_token},
    )
    assert submit_resp.status_code == 200
