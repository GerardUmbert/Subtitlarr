"""Entry point: python -m mcp_server

Runs the MCP server over the streamable-HTTP transport (not stdio) —
this process is meant to sit on the same host/container as Subtitlarr
itself and be reached over the network from a desktop MCP client (e.g.
Claude Code / VS Code), per plans/mcp-server.md."""
import asyncio
import logging

import uvicorn

from mcp_server.auth import wrap
from mcp_server.config import config
from mcp_server.server import mcp
from mcp_server.startup_auth import resolve_auth_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("mcp_server")


def main() -> None:
    asyncio.run(resolve_auth_token())
    app = wrap(mcp.streamable_http_app())
    logger.info("Starting Subtitlarr MCP server on %s:%s", config.host, config.port)
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
