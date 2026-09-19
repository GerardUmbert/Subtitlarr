FROM python:3.12-slim

LABEL org.opencontainers.image.title="Subtitlarr" \
      org.opencontainers.image.description="Fills gaps in your Bazarr subtitle library by translating subtitles you already have into languages you're missing, using a local or cloud LLM." \
      org.opencontainers.image.source="https://github.com/GerardUmbert/Subtitlarr" \
      org.opencontainers.image.licenses="AGPL-3.0"

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The MCP server (mcp_server/) is a deliberately separate process (see
# plans/mcp-server.md) — it needs its own venv, not just its own
# directory, because the `mcp` SDK pulls in a starlette version that
# conflicts with the main app's pinned FastAPI/starlette (confirmed
# during development: installing `mcp` into the main environment
# upgraded starlette and broke FastAPI's version constraint). Two
# isolated venvs in one image is the only way to run both without one
# breaking the other's dependencies.
COPY mcp_server/requirements.txt ./mcp_server-requirements.txt
RUN python -m venv /opt/mcp_venv \
    && /opt/mcp_venv/bin/pip install --no-cache-dir -r mcp_server-requirements.txt

COPY app ./app
COPY mcp_server ./mcp_server

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin subtitlarr \
    && mkdir -p /data \
    && chown -R subtitlarr:subtitlarr /app /data /opt/mcp_venv

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV DB_PATH=/data/subtitlarr.db
EXPOSE 7777
EXPOSE 7778

# Baked in at build time (see docker-release.yml) so anonymous telemetry
# works out of the box for anyone pulling the published image, with no
# setup required on their end. A locally-built image (docker build with
# no --build-arg) simply gets empty values, which telemetry.py treats as
# "disabled" — never sends anything. TELEMETRY_ENABLED still lets any
# user opt out from Settings regardless of these being set.
ARG TELEMETRY_MEASUREMENT_ID=""
ARG TELEMETRY_API_SECRET=""
ENV TELEMETRY_MEASUREMENT_ID=${TELEMETRY_MEASUREMENT_ID}
ENV TELEMETRY_API_SECRET=${TELEMETRY_API_SECRET}

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7777/healthz')" || exit 1

# Starts as root (needed to chown /data and adjust the subtitlarr user to
# match PUID/PGID) — the entrypoint itself drops to that user via gosu
# before uvicorn ever runs, so the app process is never actually root.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7777"]
