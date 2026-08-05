import json
import sqlite3
from datetime import datetime, timedelta, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clear_queue_data(conn: sqlite3.Connection) -> dict:
    """Wipes items/run_history/item_run_log and resets their autoincrement
    counters — used to recover from mismatched/confusing local state (e.g.
    after pointing at the wrong DB file, or wanting a clean re-sync from
    Bazarr) without touching app_config (Bazarr connection, engine config,
    schedule — anything the user configured through Settings). Caller is
    responsible for ensuring no run is active before calling this."""
    with conn:
        items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        runs = conn.execute("SELECT COUNT(*) FROM run_history").fetchone()[0]
        logs = conn.execute("SELECT COUNT(*) FROM item_run_log").fetchone()[0]
        conn.execute("DELETE FROM item_run_log")
        conn.execute("DELETE FROM run_history")
        conn.execute("DELETE FROM items")
        conn.execute(
            "DELETE FROM sqlite_sequence WHERE name IN ('items', 'run_history', 'item_run_log')"
        )
    return {"items_cleared": items, "runs_cleared": runs, "logs_cleared": logs}


def reset_stuck_translating_items(conn: sqlite3.Connection) -> int:
    """Items still marked 'translating' are always stale on startup — there
    is no checkpointing, so a process restart mid-batch means the in-flight
    attempt is simply gone. Resets them to 'pending' so they get picked up
    and retried on the next run rather than sitting stuck forever. Returns
    the number of items reset."""
    now = _now()
    with conn:
        cur = conn.execute(
            """
            UPDATE items
            SET status = 'pending', last_updated = ?,
                error_message = 'Interrupted by a server restart; queued for retry.'
            WHERE status = 'translating'
            """,
            (now,),
        )
        return cur.rowcount


def upsert_item_seen(
    conn: sqlite3.Connection,
    *,
    item_type: str,
    bazarr_id: int,
    series_id: int | None,
    title: str,
    series_title: str | None,
    season_episode: str | None,
    target_language: str,
) -> None:
    """Insert a (item, target_language) row if new, stamping first_seen_wanted.
    Existing rows just get last_updated refreshed — first_seen_wanted is never
    touched again, since it anchors the age-gate clock."""
    now = _now()
    with conn:
        conn.execute(
            """
            INSERT INTO items (
                item_type, bazarr_id, series_id, title, series_title,
                season_episode, target_language, status,
                first_seen_wanted, last_updated
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            ON CONFLICT (item_type, bazarr_id, target_language)
            DO UPDATE SET last_updated = excluded.last_updated
            """,
            (
                item_type,
                bazarr_id,
                series_id,
                title,
                series_title,
                season_episode,
                target_language,
                now,
                now,
            ),
        )


def mark_resolved_if_missing(
    conn: sqlite3.Connection, item_type: str, bazarr_id: int, still_missing_languages: set[str]
) -> None:
    """Items no longer in Bazarr's missing_subtitles list (Bazarr found a real
    subtitle, or the target was unmonitored) are pulled out of active
    consideration rather than left to linger as pending/queued forever."""
    now = _now()
    rows = conn.execute(
        """
        SELECT id, target_language FROM items
        WHERE item_type = ? AND bazarr_id = ?
          AND status IN ('pending', 'queued', 'failed')
        """,
        (item_type, bazarr_id),
    ).fetchall()
    with conn:
        for row in rows:
            if row["target_language"] not in still_missing_languages:
                conn.execute(
                    "UPDATE items SET status = 'done', last_updated = ? WHERE id = ?",
                    (now, row["id"]),
                )


def mark_skipped_no_source(conn: sqlite3.Connection, item_id: int) -> None:
    now = _now()
    with conn:
        conn.execute(
            "UPDATE items SET status = 'skipped_no_source', last_updated = ? WHERE id = ?",
            (now, item_id),
        )


def set_resolved_source_language(conn: sqlite3.Connection, item_id: int, source_language: str) -> None:
    """Records which source language WOULD be used to translate this item,
    without touching status — a display-only preview so the Queue UI can
    show a real language instead of '?' before any translation attempt has
    happened. The actual translate-time resolution in selector.resolve_and_gate
    re-checks fresh and is the one that's trusted; this is not a cache it
    reads from."""
    now = _now()
    with conn:
        conn.execute(
            "UPDATE items SET source_language = ?, last_updated = ? WHERE id = ? AND status = 'pending'",
            (source_language, now, item_id),
        )


def get_age_gated_queue(conn: sqlite3.Connection, age_threshold_days: int) -> list[sqlite3.Row]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=age_threshold_days)).isoformat()
    return conn.execute(
        """
        SELECT * FROM items
        WHERE status IN ('pending', 'queued') AND first_seen_wanted <= ?
        ORDER BY first_seen_wanted ASC
        """,
        (cutoff,),
    ).fetchall()


def get_full_translatable_queue(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM items
        WHERE status IN ('pending', 'queued')
        ORDER BY first_seen_wanted ASC
        """
    ).fetchall()


def get_item(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()


def get_item_by_bazarr_id(
    conn: sqlite3.Connection, item_type: str, bazarr_id: int, target_language: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM items WHERE item_type = ? AND bazarr_id = ? AND target_language = ?",
        (item_type, bazarr_id, target_language),
    ).fetchone()


def update_item_status(
    conn: sqlite3.Connection,
    item_id: int,
    status: str,
    *,
    source_language: str | None = None,
    engine_used: str | None = None,
    error_message: str | None = None,
    mark_attempt: bool = False,
    mark_completed: bool = False,
) -> None:
    now = _now()
    fields = ["status = ?", "last_updated = ?"]
    values: list = [status, now]
    if source_language is not None:
        fields.append("source_language = ?")
        values.append(source_language)
    if engine_used is not None:
        fields.append("engine_used = ?")
        values.append(engine_used)
    if error_message is not None:
        fields.append("error_message = ?")
        values.append(error_message)
    elif status in ("translating", "done"):
        # A fresh attempt or a successful completion must never leave a
        # stale error from a previous failed run visible in the UI.
        fields.append("error_message = NULL")
    if mark_attempt:
        fields.append("last_attempt_at = ?")
        values.append(now)
    if mark_completed:
        fields.append("completed_at = ?")
        values.append(now)
    values.append(item_id)
    with conn:
        conn.execute(f"UPDATE items SET {', '.join(fields)} WHERE id = ?", values)


_QUEUE_SORTS = {
    # Stable — a row's position never changes just because its status
    # changed, so clicking "run" on an item doesn't reshuffle the table
    # out from under you. Used by the Queue & History page.
    "title": "COALESCE(series_title, title) COLLATE NOCASE, season_episode, target_language",
    # Most-recently-touched first. Used by the Dashboard's "recent
    # activity" panel, where that's the entire point of the view.
    "recent": "last_updated DESC",
}


def _build_queue_filter(
    status: str | None, item_type: str | None, search: str | None
) -> tuple[list[str], list]:
    conditions: list[str] = []
    params: list = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if item_type:
        conditions.append("item_type = ?")
        params.append(item_type)
    if search:
        conditions.append("COALESCE(series_title, title) LIKE ? ESCAPE '\\'")
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
    return conditions, params


def list_queue(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    item_type: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
    sort: str = "title",
) -> tuple[list[sqlite3.Row], int]:
    order_by = _QUEUE_SORTS.get(sort, _QUEUE_SORTS["title"])
    conditions, params = _build_queue_filter(status, item_type, search)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    total = conn.execute(
        f"SELECT COUNT(*) FROM items {where}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"""
        SELECT * FROM items {where}
        ORDER BY {order_by}
        LIMIT ? OFFSET ?
        """,
        (*params, page_size, (page - 1) * page_size),
    ).fetchall()
    return rows, total


def get_translatable_queue_filtered(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    item_type: str | None = None,
    search: str | None = None,
) -> list[sqlite3.Row]:
    """Same filters as list_queue, but restricted to items that are actually
    translatable (pending/queued) and returning the FULL matched set, not a
    page — used by 'run all matching this filter' bulk actions. A status
    filter for something other than pending/queued (e.g. 'done', 'failed')
    correctly yields nothing, since those items aren't in a runnable state."""
    conditions, params = _build_queue_filter(status, item_type, search)
    conditions.append("status IN ('pending', 'queued')")
    where = f"WHERE {' AND '.join(conditions)}"
    return conn.execute(
        f"SELECT * FROM items {where} ORDER BY first_seen_wanted ASC", params
    ).fetchall()


def count_completed_today(conn: sqlite3.Connection) -> int:
    """Number of items marked 'done' since UTC midnight — used to enforce the
    daily translation cap. Based on completed_at, not last_updated, so a
    failed retry attempt doesn't count against the limit."""
    since = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM items WHERE status = 'done' AND completed_at >= ?",
        (since,),
    ).fetchone()
    return row["n"]


def get_stats(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM items GROUP BY status"
    ).fetchall()
    counts = {row["status"]: row["n"] for row in rows}
    wanted = sum(counts.values())
    return {
        "wanted": wanted,
        "translatable": counts.get("pending", 0) + counts.get("queued", 0),
        "completed": counts.get("done", 0),
        "no_source": counts.get("skipped_no_source", 0),
        "failed": counts.get("failed", 0),
        "by_status": counts,
    }


def start_run(conn: sqlite3.Connection, triggered_by: str) -> int:
    now = _now()
    with conn:
        cur = conn.execute(
            "INSERT INTO run_history (triggered_by, started_at) VALUES (?, ?)",
            (triggered_by, now),
        )
        return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, processed: int, failed: int) -> None:
    now = _now()
    with conn:
        conn.execute(
            """
            UPDATE run_history
            SET finished_at = ?, items_processed = ?, items_failed = ?
            WHERE id = ?
            """,
            (now, processed, failed, run_id),
        )


def log_item_attempt(
    conn: sqlite3.Connection,
    item_id: int,
    run_id: int | None,
    status: str,
    engine_used: str | None = None,
    error_message: str | None = None,
    settings_snapshot: dict | None = None,
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO item_run_log
                (item_id, run_id, status, engine_used, error_message, settings_snapshot, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id, run_id, status, engine_used, error_message,
                json.dumps(settings_snapshot) if settings_snapshot is not None else None,
                _now(),
            ),
        )


def get_config(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value_json FROM app_config WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return json.loads(row["value_json"])


def set_config(conn: sqlite3.Connection, key: str, value) -> None:
    now = _now()
    with conn:
        conn.execute(
            """
            INSERT INTO app_config (key, value_json, updated_at) VALUES (?, ?, ?)
            ON CONFLICT (key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
            """,
            (key, json.dumps(value), now),
        )
