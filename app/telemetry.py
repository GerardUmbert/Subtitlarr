"""Anonymous usage telemetry: a daily ping to a GA4 property so the
maintainer can see rough install counts and usage patterns across the
installed base. Opt-in-by-default but fully user-toggleable from
Settings, and a no-op entirely unless the deploying maintainer has set
telemetry_measurement_id/telemetry_api_secret (a fresh build from source
with no GA credentials never sends anything, regardless of the toggle).

What's sent: a random per-install UUID (not derived from hardware, IP, or
anything identifying), the app version, OS platform, which translation
engine provider types are configured (e.g. "ollama", "gemini" — never
names, hosts, or API keys), current queue state, and translation activity
since the last ping. Never sent: Bazarr URL/API key, engine API keys or
instance names, file paths, subtitle content, or anything else
content-identifying.

Cumulative counters (completed/failed items, run count) are sent as
DELTAS since the last successful ping, not lifetime snapshots — this
lets GA4's own event-count/sum aggregation give a correct global total
across every install without needing to de-duplicate per-instance
snapshots afterward. Queue-size fields (items_total/items_pending) are
current live state, not a running total, so those stay as snapshots —
a "delta" of queue size wouldn't mean anything sensible."""
import logging
import platform
import sqlite3
import uuid

import httpx

from app import __version__, state
from app.config import Settings
from app.db import repository
from app.db.engine_instances_repo import SEPARATOR_TYPE

logger = logging.getLogger(__name__)

_GA4_ENDPOINT = "https://www.google-analytics.com/mp/collect"
_INSTANCE_ID_KEY = "telemetry.instance_id"

# Last-sent values for the cumulative counters, so the next ping can send
# only what changed since then instead of the lifetime total again.
_LAST_SENT_COMPLETED_KEY = "telemetry.last_sent.items_completed"
_LAST_SENT_FAILED_KEY = "telemetry.last_sent.items_failed"
_LAST_SENT_RUNS_KEY = "telemetry.last_sent.translation_runs_total"


def _get_or_create_instance_id(conn: sqlite3.Connection) -> str:
    instance_id = repository.get_config(conn, _INSTANCE_ID_KEY, default=None)
    if instance_id is None:
        instance_id = str(uuid.uuid4())
        repository.set_config(conn, _INSTANCE_ID_KEY, instance_id)
    return instance_id


def _configured_engine_types(conn: sqlite3.Connection) -> str:
    """Comma-separated, not a list — GA4's Measurement Protocol event
    params reject array values outright (silently, behind a 204) and
    only accept strings/numbers/booleans."""
    rows = conn.execute(
        "SELECT DISTINCT provider_type FROM engine_instances WHERE provider_type != ?",
        (SEPARATOR_TYPE,),
    ).fetchall()
    return ",".join(sorted(row["provider_type"] for row in rows))


def build_payload(conn: sqlite3.Connection) -> dict:
    stats = repository.get_stats(conn)
    total_runs = conn.execute("SELECT COUNT(*) FROM run_history").fetchone()[0]

    last_completed = repository.get_config(conn, _LAST_SENT_COMPLETED_KEY, default=0)
    last_failed = repository.get_config(conn, _LAST_SENT_FAILED_KEY, default=0)
    last_runs = repository.get_config(conn, _LAST_SENT_RUNS_KEY, default=0)

    return {
        "instance_id": _get_or_create_instance_id(conn),
        "app_version": __version__,
        "os_platform": platform.system(),
        "engine_types": _configured_engine_types(conn),
        "items_total": stats["wanted"],
        "items_pending": stats["translatable"],
        "items_completed_delta": max(0, stats["completed"] - last_completed),
        "items_failed_delta": max(0, stats["failed"] - last_failed),
        "translation_runs_delta": max(0, total_runs - last_runs),
        "_current_completed": stats["completed"],
        "_current_failed": stats["failed"],
        "_current_runs": total_runs,
    }


async def send_ping(conn: sqlite3.Connection, settings: Settings, *, triggered_by: str = "cron") -> None:
    """Fire-and-forget: any failure (network, GA4 rejecting the payload) is
    logged and swallowed. Telemetry must never be able to break a
    scheduled run or crash the app.

    Only advances the stored "last sent" counters on a successful send —
    if the request fails, the next ping's delta correctly includes
    whatever would have been missed, instead of silently dropping it.

    Every attempt (skipped, sent, or failed) is recorded as a job_events
    row — same visibility every other cron job (backup, sync_media, ...)
    already gets on the Jobs/History page. Before this, a successful
    ping had NO visible trace anywhere; the only log line was a warning
    on failure, so silence was ambiguous between "it worked" and "the
    cron never fired at all"."""
    if not settings.telemetry_enabled:
        with state.db_lock:
            event_id = repository.start_job_event(conn, "telemetry", triggered_by=triggered_by)
            repository.finish_job_event(conn, event_id, status="done", result="skipped (disabled in Settings)")
        return
    if not settings.telemetry_measurement_id or not settings.telemetry_api_secret:
        with state.db_lock:
            event_id = repository.start_job_event(conn, "telemetry", triggered_by=triggered_by)
            repository.finish_job_event(conn, event_id, status="done", result="skipped (no GA4 credentials configured)")
        return

    with state.db_lock:
        event_id = repository.start_job_event(conn, "telemetry", triggered_by=triggered_by)

    payload = build_payload(conn)
    instance_id = payload.pop("instance_id")
    current_completed = payload.pop("_current_completed")
    current_failed = payload.pop("_current_failed")
    current_runs = payload.pop("_current_runs")

    body = {
        "client_id": instance_id,
        "events": [
            {
                "name": "daily_usage_ping",
                "params": payload,
            }
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                _GA4_ENDPOINT,
                params={
                    "measurement_id": settings.telemetry_measurement_id,
                    "api_secret": settings.telemetry_api_secret,
                },
                json=body,
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Telemetry ping failed (non-fatal): %s", exc)
        with state.db_lock:
            repository.finish_job_event(conn, event_id, status="failed", error=str(exc))
        return

    repository.set_config(conn, _LAST_SENT_COMPLETED_KEY, current_completed)
    repository.set_config(conn, _LAST_SENT_FAILED_KEY, current_failed)
    repository.set_config(conn, _LAST_SENT_RUNS_KEY, current_runs)

    with state.db_lock:
        repository.finish_job_event(
            conn, event_id, status="done",
            result=(
                f"sent: +{payload['items_completed_delta']} completed, "
                f"+{payload['items_failed_delta']} failed, "
                f"+{payload['translation_runs_delta']} run(s)"
            ),
        )
