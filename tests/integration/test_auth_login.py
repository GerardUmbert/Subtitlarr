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
        # app.auth.session.check_csrf rejects a mutating, session-
        # authenticated request with no Origin/Referer (or a mismatched
        # one) — TestClient sends neither by default, and always uses
        # Host: testserver (its fixed base_url), so this is the one
        # Origin value that will ever look same-origin to it. This file
        # tests the login flow's own individual steps directly rather
        # than going through the shared conftest.log_in helper, so it
        # sets this itself instead of importing that helper.
        c.headers["Origin"] = "http://testserver"
        yield c


def test_login_page_loads_when_logged_out(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "Log in" in resp.text


def test_wrong_password_rejected(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401


def test_unknown_username_rejected(client):
    resp = client.post("/api/auth/login", json={"username": "nobody", "password": "admin"})
    assert resp.status_code == 401


def test_correct_default_login_redirects_to_change_password(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert resp.status_code == 200
    assert resp.json() == {"redirect": "/change-password"}


def test_change_password_rejects_same_as_default(client):
    client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": "admin", "new_password": "admin"},
    )
    assert resp.status_code == 422


def test_change_password_rejects_wrong_current_password(client):
    client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    resp = client.post(
        "/api/auth/change-password",
        json={"current_password": "not-the-real-one", "new_password": "realpassword123"},
    )
    assert resp.status_code == 401


def test_full_login_and_password_change_flow(client):
    login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert login.json()["redirect"] == "/change-password"

    changed = client.post(
        "/api/auth/change-password",
        json={"current_password": "admin", "new_password": "realpassword123"},
    )
    assert changed.status_code == 200
    assert changed.json() == {"redirect": "/"}

    # Old default password no longer works after the change.
    stale = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    assert stale.status_code == 401

    # New password logs in cleanly, no forced redirect this time.
    relogin = client.post("/api/auth/login", json={"username": "admin", "password": "realpassword123"})
    assert relogin.status_code == 200
    assert relogin.json() == {"redirect": "/"}


def test_login_page_redirects_away_once_logged_in(client):
    client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    resp = client.get("/login")
    # TestClient follows redirects by default; final destination after a
    # still-pending forced password change is /change-password.
    assert resp.status_code == 200
    assert "new password" in resp.text.lower()


def test_logout_clears_session(client):
    client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    client.post(
        "/api/auth/change-password",
        json={"current_password": "admin", "new_password": "realpassword123"},
    )
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json() == {"redirect": "/login"}

    # A follow-up page load is no longer treated as logged in.
    login_page = client.get("/login")
    assert login_page.status_code == 200
    assert "Log in" in login_page.text


def test_login_rate_limited_after_repeated_failures(client):
    for _ in range(5):
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert resp.status_code == 401
    limited = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert limited.status_code == 429


@pytest.mark.parametrize("path", [
    "/", "/queue", "/engines", "/languages", "/bazarr", "/settings",
    "/jobs", "/history", "/compare", "/mcp-server", "/external-translate",
])
def test_page_redirects_to_login_when_logged_out(client, path):
    resp = client.get(path, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.parametrize("path", [
    "/", "/queue", "/engines", "/languages", "/bazarr", "/settings",
    "/jobs", "/history", "/compare", "/mcp-server", "/external-translate",
])
def test_page_redirects_to_change_password_when_forced_and_not_yet_changed(client, path):
    client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    resp = client.get(path, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/change-password"


def test_page_loads_normally_once_logged_in_and_password_changed(client):
    client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    client.post(
        "/api/auth/change-password",
        json={"current_password": "admin", "new_password": "realpassword123"},
    )
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200


def test_login_page_itself_never_redirects_to_login(client):
    """/login must stay reachable while logged out — otherwise a logged-
    out visitor could never reach the one page that lets them log in."""
    resp = client.get("/login", follow_redirects=False)
    assert resp.status_code == 200


def test_change_password_page_reachable_mid_forced_change(client):
    """/change-password must stay reachable even though require_session_page
    would otherwise redirect there — it's the destination, not a route
    guarded by the same dependency (see app/api/auth.py's module
    docstring: applying require_session_page to this route would be a
    self-redirect loop)."""
    client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    resp = client.get("/change-password", follow_redirects=False)
    assert resp.status_code == 200
