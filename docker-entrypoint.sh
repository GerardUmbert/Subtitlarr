#!/bin/sh
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if [ "$(id -u)" = "0" ]; then
    # Adjust the baked-in subtitlarr user/group to match whatever uid/gid
    # the host actually wants (e.g. Unraid's nobody:users, 99:100) — a
    # bind-mounted /data's ownership comes from the HOST, not the image's
    # own build-time chown, so a fixed uid baked into the image is often
    # wrong on first run. Mirrors the PUID/PGID pattern LinuxServer.io
    # images use, which most Unraid users already expect.
    groupmod -o -g "$PGID" subtitlarr
    usermod -o -u "$PUID" subtitlarr
    chown -R subtitlarr:subtitlarr /data
    exec gosu subtitlarr "$0" "$@"
fi

# The MCP server (see mcp_server/, plans/mcp-server.md) runs as a sibling
# background process in its own venv (/opt/mcp_venv) — deliberately
# separate from the main app's dependencies (see Dockerfile's comment on
# why) and from the main app's own process/event loop, so a bug or a
# hung tool call in it can never stall or crash live translation runs.
# MCP_ENABLED lets it be turned off entirely (e.g. no MCP_AUTH_TOKEN set
# and the operator doesn't want an unauthenticated listener at all).
if [ "${MCP_ENABLED:-true}" = "true" ]; then
    SUBTITLARR_BASE_URL="${SUBTITLARR_BASE_URL:-http://127.0.0.1:7777}" \
        /opt/mcp_venv/bin/python -m mcp_server &
fi

exec "$@"
