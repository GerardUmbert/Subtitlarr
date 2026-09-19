"""Resolves the auth token to enforce, at process startup.

If MCP_AUTH_TOKEN is set explicitly, that wins outright (lets an
operator pin a token independent of Subtitlarr's own generated one, or
run this process against a Subtitlarr instance whose /api/mcp/status
isn't reachable yet for some other reason). Otherwise, fetches it from
Subtitlarr's own GET /api/mcp/status — that endpoint generates the
token on first call and persists it in Subtitlarr's DB, so both
processes agree on the same value without either baking the other's
secret into a build. Retries with backoff since this process is
typically started by the same container entrypoint as Subtitlarr
itself and may well win the race to be ready first."""
import asyncio
import logging

import httpx

from mcp_server.config import config

logger = logging.getLogger("mcp_server.startup_auth")

_MAX_ATTEMPTS = 10
_RETRY_SECONDS = 3.0


async def resolve_auth_token() -> None:
    if config.auth_token:
        logger.info("Using explicitly configured MCP_AUTH_TOKEN.")
        return

    async with httpx.AsyncClient(base_url=config.subtitlarr_base_url, timeout=10.0) as http:
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                resp = await http.get("/api/mcp/status")
                resp.raise_for_status()
                token = resp.json()["token"]
                config.auth_token = token
                logger.info("Fetched auth token from Subtitlarr (%s).", config.subtitlarr_base_url)
                return
            except Exception as exc:  # noqa: BLE001 - retry regardless of failure shape
                logger.warning(
                    "Could not fetch auth token from Subtitlarr (attempt %d/%d): %s",
                    attempt, _MAX_ATTEMPTS, exc,
                )
                if attempt < _MAX_ATTEMPTS:
                    await asyncio.sleep(_RETRY_SECONDS)

    logger.error(
        "Giving up fetching the auth token from Subtitlarr after %d attempts — "
        "every request will be rejected (fail-closed) until this process is restarted "
        "once Subtitlarr is reachable, or MCP_AUTH_TOKEN is set explicitly.",
        _MAX_ATTEMPTS,
    )
