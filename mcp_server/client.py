"""Thin HTTP client against Subtitlarr's own REST API (app/api/*.py) —
the MCP server never touches the database or filesystem directly, only
ever the same public endpoints the web UI itself calls. See
plans/mcp-server.md for why this stays a separate process talking HTTP,
rather than importing app.* in-process."""
import httpx

from mcp_server.config import config


class SubtitlarrClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=config.subtitlarr_base_url, timeout=config.request_timeout_seconds
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(self, path: str, params: dict | None = None) -> dict:
        resp = await self._client.get(path, params=_clean(params))
        return _to_result(resp)

    async def post(self, path: str, json: dict | None = None, params: dict | None = None) -> dict:
        resp = await self._client.post(path, json=json, params=_clean(params))
        return _to_result(resp)


def _clean(params: dict | None) -> dict | None:
    """Drops None values so an unset optional filter (e.g. status=None)
    doesn't get serialized as the literal string "None" in the query
    string — FastAPI's query-param parsing on the other end expects the
    param to be absent, not a stringified null."""
    if params is None:
        return None
    return {k: v for k, v in params.items() if v is not None}


def _to_result(resp: httpx.Response) -> dict:
    """Never raises on a non-2xx — an HTTP error from Subtitlarr's API
    (404 item not found, 422 no usable source, 409 run already active)
    is information the calling agent needs to see and reason about, not
    a Python exception that aborts the whole tool call with a generic
    traceback. Mirrors the {"started": False, "reason": ...} pattern
    Subtitlarr's own endpoints already use for expected failure paths."""
    try:
        body = resp.json()
    except ValueError:
        body = {"detail": resp.text}
    if resp.status_code >= 400:
        return {"error": True, "status_code": resp.status_code, "detail": body.get("detail", body)}
    return body


client = SubtitlarrClient()
