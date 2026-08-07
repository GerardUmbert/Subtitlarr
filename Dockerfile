FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin subtitlarr \
    && mkdir -p /data \
    && chown -R subtitlarr:subtitlarr /app /data

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV DB_PATH=/data/subtitlarr.db
EXPOSE 7777

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7777/healthz')" || exit 1

# Starts as root (needed to chown /data and adjust the subtitlarr user to
# match PUID/PGID) — the entrypoint itself drops to that user via gosu
# before uvicorn ever runs, so the app process is never actually root.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7777"]
