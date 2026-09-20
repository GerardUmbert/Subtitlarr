"""Stage 4 of AUTH_PLAN.md (local, untracked design doc): CSRF protection
for session-cookie-authenticated mutating requests. Only meaningful for
the session path — a bearer-token request can't be forged the same way a
cookie auto-attaches cross-site, so those are exempt (see
app.auth.session.check_csrf's own docstring)."""
import pytest
from fastapi.testclient import TestClient

from app import state
from app.config import settings
from app.main import app

from tests.integration.conftest import log_in


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "bazarr_base_url", "http://bazarr.test:6767")
    monkeypatch.setattr(settings, "bazarr_api_key", "testkey")
    with TestClient(app) as c:
        yield c


def test_mutating_request_with_no_origin_or_referer_rejected(client):
    log_in(client)
    del client.headers["origin"]
    resp = client.post("/api/config/languages", json={"source_priority": ["en"]})
    assert resp.status_code == 403


def test_mutating_request_with_mismatched_origin_rejected(client):
    log_in(client)
    resp = client.post(
        "/api/config/languages",
        json={"source_priority": ["en"]},
        headers={"Origin": "http://evil.example.com"},
    )
    assert resp.status_code == 403


def test_mutating_request_with_matching_origin_succeeds(client):
    log_in(client)
    resp = client.post("/api/config/languages", json={"source_priority": ["en"]})
    assert resp.status_code == 200


def test_get_request_never_needs_origin(client):
    log_in(client)
    del client.headers["origin"]
    resp = client.get("/api/config/languages")
    assert resp.status_code == 200


def test_bearer_token_request_exempt_from_csrf_check(client):
    """A script authenticating via the MCP bearer token never sends a
    browser-style Origin header at all — it must not be rejected for
    that, since CSRF only exploits automatic cookie attachment, which a
    bearer token never has."""
    from app.api.mcp import get_or_create_token

    token = get_or_create_token(state.get_conn())
    resp = client.post(
        "/api/config/languages",
        json={"source_priority": ["en"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


def test_logout_rejected_without_matching_origin(client):
    log_in(client)
    resp = client.post("/api/auth/logout", headers={"Origin": "http://evil.example.com"})
    assert resp.status_code == 403


def test_change_password_rejected_without_matching_origin(client):
    client.headers["Origin"] = "http://testserver"
    client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": "admin", "new_password": "realpassword123"},
        headers={"Origin": "http://evil.example.com"},
    )
    assert resp.status_code == 403


def test_login_itself_has_no_csrf_check(client):
    """Login only ever establishes a session, never acts on an existing
    one — there's nothing for CSRF to exploit here (a forged cross-site
    login would just log the attacker's own known credentials into the
    victim's browser, gaining the attacker nothing), so it's exempt by
    design, unlike logout/change-password."""
    resp = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
        headers={"Origin": "http://evil.example.com"},
    )
    assert resp.status_code == 200
