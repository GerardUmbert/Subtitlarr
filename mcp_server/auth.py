"""Bearer-token check wrapped around the mounted MCP ASGI app.

Much simpler than a standalone-process design would need: since this
runs in the same process as the main app, the current token is just
read straight from the DB via app.api.mcp's existing helper — no HTTP
round-trip, no startup race, no separate token-sync mechanism. Still
required even though it's on the same port as the web UI: an MCP tool
call can trigger a real translation run or push to Bazarr, which
shouldn't be reachable by anything that can merely load the dashboard
if this port is ever exposed more broadly than intended (reverse
proxy misconfiguration, etc.)."""
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app import state
from app.api.mcp import get_or_create_token


class BearerAuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        auth_header = headers.get(b"authorization", b"").decode("latin-1")
        expected = f"Bearer {get_or_create_token(state.get_conn())}"
        if auth_header != expected:
            response = JSONResponse({"error": "Unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def wrap(app: ASGIApp) -> ASGIApp:
    return BearerAuthMiddleware(app)
