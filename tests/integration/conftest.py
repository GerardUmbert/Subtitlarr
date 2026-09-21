"""Shared helper for integration tests: most app/api/*.py routers now
require an authenticated session or a valid bearer token (see
AUTH_PLAN.md, a local untracked design doc) — every existing TestClient-
based test needs to actually log in before hitting a real route."""


def log_in(client) -> None:
    """Completes the full login + forced-password-change flow so the
    client's session cookie is fully authenticated (not just logged in
    but still must_change_password=True, which require_session_or_mcp_token
    would still reject).

    Also sets a same-origin Origin header on the client for every request
    from here on — app.auth.session.check_csrf now rejects a mutating,
    session-authenticated request with no Origin/Referer at all (or one
    that doesn't match the request's own Host), and TestClient sends
    neither header by default. TestClient always uses Host: testserver
    (its fixed base_url), so this is the one Origin value that will ever
    look same-origin to it."""
    client.headers["Origin"] = "http://testserver"
    client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    client.post(
        "/api/auth/change-password",
        json={"current_password": "admin", "new_password": "test-password-123"},
    )
