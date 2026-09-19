"""Bearer-token check wrapped around the MCP server's ASGI app.

Necessary because this runs the network (streamable-http) transport,
not stdio — stdio is implicitly trusted (the client spawned this exact
process as its own child), but a network listener reachable from a
desktop over to a NAS is not. See plans/mcp-server.md, "Deployment
target" section, for why this is a hard requirement here rather than
an optional hardening step."""
import time

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp_server.config import config

# A mismatched token triggers a re-fetch from Subtitlarr's own
# /api/mcp/status before rejecting, so that regenerating the token there
# (POST /api/mcp/regenerate-token) takes effect quickly — without this, a
# leaked token that was "regenerated away" would keep working against
# this process until its next restart, defeating the whole point of
# regenerating it. Rate-limited so a client hammering this server with a
# wrong token can't turn every rejected request into an extra call
# against Subtitlarr — the tradeoff is that revocation is only guaranteed
# within this cooldown window, not instantly: a just-revoked token
# presented within _REFETCH_COOLDOWN_SECONDS of an unrelated failed
# refetch attempt (e.g. from a different client's bad token) can still
# be accepted once more before the next refetch happens.
_REFETCH_COOLDOWN_SECONDS = 5.0
_last_refetch_attempt = 0.0


async def _refetch_token() -> None:
    global _last_refetch_attempt
    now = time.monotonic()
    if now - _last_refetch_attempt < _REFETCH_COOLDOWN_SECONDS:
        return
    _last_refetch_attempt = now
    try:
        async with httpx.AsyncClient(base_url=config.subtitlarr_base_url, timeout=5.0) as http:
            resp = await http.get("/api/mcp/status")
            resp.raise_for_status()
            config.auth_token = resp.json()["token"]
    except Exception:  # noqa: BLE001 - keep the old (possibly still-valid) token on any failure
        pass


class BearerAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        auth_header = headers.get(b"authorization", b"").decode("latin-1")

        if not config.auth_token or auth_header != f"Bearer {config.auth_token}":
            # Either no token is cached yet (startup fetch never succeeded)
            # or what was presented doesn't match what's cached — in both
            # cases, check Subtitlarr's CURRENT token once before giving
            # up, since it may have just been regenerated or Subtitlarr may
            # have only just become reachable.
            await _refetch_token()

        if not config.auth_token:
            # Still nothing — fail CLOSED, not open. Every request is
            # rejected until a real token is available, rather than
            # briefly accepting anything.
            response = JSONResponse(
                {"error": "MCP server has no auth token configured yet — Subtitlarr may still be starting up"},
                status_code=503,
            )
            await response(scope, receive, send)
            return

        if auth_header != f"Bearer {config.auth_token}":
            response = JSONResponse({"error": "Unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def wrap(app: Starlette) -> ASGIApp:
    return BearerAuthMiddleware(app)
