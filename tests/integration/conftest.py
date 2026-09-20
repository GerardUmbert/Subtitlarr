"""Shared helper for integration tests: most app/api/*.py routers now
require an authenticated session or a valid bearer token (see
AUTH_PLAN.md, a local untracked design doc) — every existing TestClient-
based test needs to actually log in before hitting a real route."""


def log_in(client) -> None:
    """Completes the full login + forced-password-change flow so the
    client's session cookie is fully authenticated (not just logged in
    but still must_change_password=True, which require_session_or_mcp_token
    would still reject)."""
    client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    client.post(
        "/api/auth/change-password",
        json={"current_password": "admin", "new_password": "test-password-123"},
    )
