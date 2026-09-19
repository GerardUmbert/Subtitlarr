"""Deploy-time config for the MCP server process — deliberately separate
from app.config.Settings, since this is a fully independent process (see
plans/mcp-server.md, Option A) that talks to Subtitlarr only over HTTP,
never importing app.* directly."""
import os


class Config:
    # Where the already-running Subtitlarr instance's REST API lives.
    # Defaults to the sibling process on the same host/container.
    subtitlarr_base_url: str = os.environ.get("SUBTITLARR_BASE_URL", "http://127.0.0.1:7777")

    # The bearer token every request must present. If MCP_AUTH_TOKEN isn't
    # set explicitly, it's fetched from Subtitlarr's own GET /api/mcp/status
    # at startup (see startup_auth.fetch_token_if_needed) — that endpoint
    # generates the token on first call and persists it in Subtitlarr's DB,
    # so the two processes agree on one value without either baking the
    # other's secret in at image-build time. Only ever empty if Subtitlarr
    # itself was unreachable at startup, which auth.py's middleware treats
    # as "reject everything," not "allow everything" — see auth.py.
    auth_token: str = os.environ.get("MCP_AUTH_TOKEN", "")

    host: str = os.environ.get("MCP_HOST", "0.0.0.0")
    port: int = int(os.environ.get("MCP_PORT", "7778"))

    # Plain HTTP request timeout to Subtitlarr's own API — generous
    # because run-triggering endpoints return immediately (fire-and-
    # forget, per app/state.py's spawn_background_task pattern) but a
    # poll/sync-triggering endpoint's underlying Bazarr call can be slow.
    request_timeout_seconds: float = 30.0


config = Config()
