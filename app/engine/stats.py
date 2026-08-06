"""Aggregate stats for the History page's Stats tab — items per language,
queue totals, per-engine timing/fail-ratio, fallback counts. Item counts
and fail ratios come from the DB (items/item_run_log); per-engine timing
and fallback counts come from the rotating log file via log_events, since
item_run_log only stores one terminal timestamp per attempt, not
individual call durations or which engine a fallback landed on.
"""

import re
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone

from app.engine import log_events

_RANGE_DAYS = {"7d": 7, "30d": 30}
_RESPONSE_SECONDS_RE = re.compile(r"took (?P<seconds>[\d.]+)s$")


def _range_cutoff(range_: str) -> str | None:
    days = _RANGE_DAYS.get(range_)
    if days is None:
        return None
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _items_per_language(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT target_language, COUNT(*) AS n
        FROM items
        GROUP BY target_language
        ORDER BY n DESC
        """
    ).fetchall()
    return [{"language": r["target_language"], "count": r["n"]} for r in rows]


def _status_totals(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT status, COUNT(*) AS n
        FROM items
        GROUP BY status
        ORDER BY n DESC
        """
    ).fetchall()
    return [{"status": r["status"], "count": r["n"]} for r in rows]


def _fail_ratio_per_engine(conn: sqlite3.Connection, cutoff: str | None) -> list[dict]:
    query = """
        SELECT
            engine_used AS engine,
            COUNT(*) AS attempts,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failures
        FROM item_run_log
        WHERE engine_used IS NOT NULL
    """
    params: tuple = ()
    if cutoff is not None:
        query += " AND created_at >= ?"
        params = (cutoff,)
    query += " GROUP BY engine_used ORDER BY attempts DESC"
    rows = conn.execute(query, params).fetchall()
    return [
        {
            "engine": r["engine"],
            "attempts": r["attempts"],
            "failures": r["failures"],
            "fail_ratio": round(r["failures"] / r["attempts"], 4) if r["attempts"] else 0.0,
        }
        for r in rows
    ]


def _duration_and_fallback_stats(
    conn: sqlite3.Connection, cutoff: str | None
) -> tuple[list[dict], list[dict]]:
    """Walks the log file once, collecting each 'response' event's
    self-reported call duration (the "took %.2fs" it already logs) per
    engine, and counting fallback transitions. Aggregates p50/p90 rather
    than a mean so a handful of slow watchdog-timeout retries don't skew
    the typical-case number, without having to hand-pick and exclude
    specific outliers.

    Only 'response' events for items that ultimately succeeded are counted
    towards durations — an attempt that got a fast reply but was then
    rejected (content-blocked, alignment failure, etc.) isn't a
    representative "this engine answers in Xs" data point, and mixing the
    two in made percentiles read as nonsense (a provider retried instantly
    after a 4xx would show as sub-second alongside its real multi-second
    successful calls)."""
    succeeded_item_ids = {
        r["item_id"]
        for r in conn.execute(
            "SELECT DISTINCT item_id FROM item_run_log WHERE status = 'done'"
        ).fetchall()
    }

    events = log_events.read_events(limit=1_000_000)
    if cutoff is not None:
        events = [e for e in events if e.timestamp >= cutoff.replace("T", " ")[:23]]

    durations_by_engine: dict[str, list[float]] = {}
    fallback_counts: dict[str, int] = {}

    for e in events:
        if e.event_type == "response" and e.engine is not None:
            if e.item_id not in succeeded_item_ids:
                continue
            m = _RESPONSE_SECONDS_RE.search(e.raw)
            if m:
                durations_by_engine.setdefault(e.engine, []).append(float(m.group(1)))
        elif e.event_type in ("content_blocked_fallback", "provider_failed_fallback"):
            pair = f"{e.engine}→{e.fallback_engine or 'unknown'}"
            fallback_counts[pair] = fallback_counts.get(pair, 0) + 1

    duration_stats = []
    for engine, values in durations_by_engine.items():
        values.sort()
        duration_stats.append(
            {
                "engine": engine,
                "calls": len(values),
                "p50_seconds": round(statistics.median(values), 2),
                "p90_seconds": round(values[int(len(values) * 0.9) - 1], 2) if values else 0.0,
            }
        )
    duration_stats.sort(key=lambda d: d["calls"], reverse=True)

    fallback_stats = [
        {"from_to": pair, "count": n}
        for pair, n in sorted(fallback_counts.items(), key=lambda kv: kv[1], reverse=True)
    ]
    return duration_stats, fallback_stats


def compute_stats(conn: sqlite3.Connection, *, range_: str = "all") -> dict:
    cutoff = _range_cutoff(range_)
    duration_stats, fallback_stats = _duration_and_fallback_stats(conn, cutoff)
    return {
        "range": range_,
        "items_per_language": _items_per_language(conn),
        "status_totals": _status_totals(conn),
        "fail_ratio_per_engine": _fail_ratio_per_engine(conn, cutoff),
        "duration_per_engine": duration_stats,
        "fallback_counts": fallback_stats,
    }
