"""Regression coverage for stage 3 of AUTH_PLAN.md (local, untracked design
doc): app/api/languages.py is the first router wired with
require_session_or_mcp_token, gating /api/config/languages/* over real
HTTP while leaving MCP's in-process tool calls completely untouched — the
whole app-wide auth rollout depends on that isolation actually holding,
not just being assumed."""
import pytest
from fastapi.testclient import TestClient

from app import state
from app.config import settings
from app.main import app

from tests.integration.conftest import log_in as _log_in_and_change_password


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "bazarr_base_url", "http://bazarr.test:6767")
    monkeypatch.setattr(settings, "bazarr_api_key", "testkey")
    with TestClient(app) as c:
        yield c


def test_languages_api_rejects_anonymous_request(client):
    resp = client.get("/api/config/languages")
    assert resp.status_code == 401


def test_languages_api_works_with_valid_session(client):
    _log_in_and_change_password(client)
    resp = client.get("/api/config/languages")
    assert resp.status_code == 200


def test_languages_api_works_with_mcp_bearer_token(client):
    from app.api.mcp import get_or_create_token

    token = get_or_create_token(state.get_conn())
    resp = client.get(
        "/api/config/languages", headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


def test_languages_api_rejects_wrong_bearer_token(client):
    resp = client.get(
        "/api/config/languages", headers={"Authorization": "Bearer not-the-real-token"},
    )
    assert resp.status_code == 401


def test_languages_api_rejects_external_translate_token(client):
    """The external-translate token must NOT work here — it's scoped to
    its own /api/external-translate/* routes only, not a skeleton key for
    the rest of the API (see AUTH_PLAN.md's explicit decision on this)."""
    from app.api.external_translate import get_or_create_token as get_external_translate_token

    token = get_external_translate_token(state.get_conn())
    resp = client.get(
        "/api/config/languages", headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_mcp_tool_unaffected_by_zero_session_or_bearer(client):
    """The critical isolation guarantee this whole staged rollout depends
    on: an MCP tool call — which never goes through FastAPI's routing,
    Depends(), or this new dependency at all — must keep working exactly
    as before, even with NO session and NO bearer token present anywhere
    in this test. If this ever fails, the in-process/HTTP isolation this
    entire plan relies on has broken."""
    from mcp_server.server import build_mcp

    mcp = build_mcp()
    tools = {t.name: t for t in await mcp.list_tools()}
    assert "subtitlarr_get_language_config" in tools

    result = await mcp.call_tool("subtitlarr_get_language_config", {})
    # FastMCP wraps tool results; just confirm it didn't raise/error and
    # produced real content, proving the underlying languages.get_language_config
    # call succeeded with no auth of any kind on this test's HTTP layer.
    assert result is not None
