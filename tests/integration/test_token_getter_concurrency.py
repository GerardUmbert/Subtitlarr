"""Regression test for a real bug hit live during AUTH_PLAN.md's stage 3
(local, untracked design doc): app.api.mcp.get_or_create_token and
app.api.external_translate.get_or_create_token read/write the shared
sqlite3.Connection without holding app.state.db_lock — invisible before
require_session_or_mcp_token started calling one of them on EVERY API
request, but confirmed live under real browser traffic (a long-polling
/api/run/events loop plus this dependency firing per-request) to raise
sqlite3.InterfaceError: bad parameter or other API misuse. Reproduced here
with real concurrent threads hitting an authenticated route repeatedly,
since a single-threaded/mocked test wouldn't exercise the actual race."""
import threading

import pytest
from fastapi.testclient import TestClient

from app import state
from app.config import settings
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "bazarr_base_url", "http://bazarr.test:6767")
    monkeypatch.setattr(settings, "bazarr_api_key", "testkey")
    with TestClient(app) as c:
        yield c


def test_concurrent_authenticated_requests_dont_hit_sqlite_interface_error(client):
    from app.api.mcp import get_or_create_token

    token = get_or_create_token(state.get_conn())
    headers = {"Authorization": f"Bearer {token}"}

    errors = []

    def _hit():
        for _ in range(20):
            try:
                resp = client.get("/api/config/languages", headers=headers)
                if resp.status_code >= 500:
                    errors.append(resp.text)
            except Exception as exc:  # pragma: no cover - failure path only
                errors.append(str(exc))

    threads = [threading.Thread(target=_hit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent requests raised: {errors[:3]}"


def _log_in_and_change_password(client):
    client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    client.post(
        "/api/auth/change-password",
        json={"current_password": "admin", "new_password": "realpassword123"},
    )


def test_concurrent_mixed_routes_dont_hang_or_error(client):
    """Broader stress test across several of the newly-locked routers at
    once (queue.py, dashboard.py, history.py, schedule.py) — not just the
    single route the original bug was caught on. A join() with a timeout
    catches a deadlock (test would otherwise hang forever) rather than
    just an exception."""
    _log_in_and_change_password(client)

    errors = []

    def _hit(path):
        for _ in range(15):
            try:
                resp = client.get(path)
                if resp.status_code >= 500:
                    errors.append((path, resp.text))
            except Exception as exc:  # pragma: no cover - failure path only
                errors.append((path, str(exc)))

    paths = ["/api/queue", "/api/stats", "/api/history", "/api/config/schedule"]
    threads = [threading.Thread(target=_hit, args=(p,)) for p in paths for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert all(not t.is_alive() for t in threads), "A thread never finished — possible deadlock"
    assert not errors, f"Concurrent requests raised: {errors[:3]}"
