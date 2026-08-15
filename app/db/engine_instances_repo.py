"""CRUD and cascade-query functions for engine_instances — the ordered
list of named, independently-configured translation engines that
replaces the old single active_engine/fallback_engine settings pair.
Kept separate from repository.py (already large and covering unrelated
tables) since this is a fairly self-contained feature.

A "separator" row (provider_type='separator') marks where the cascade
walk stops — everything after it is never tried as a fallback. See
plans/multiple-engine-instances-cascade.md for the full design."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

SEPARATOR_TYPE = "separator"

# Consecutive ProviderRateLimitedError failures (against the SAME
# instance, with no intervening success) before that instance is marked
# rate-limited for RATE_LIMIT_COOLDOWN_HOURS. Deliberately not a usage/
# quota meter — see plans/multiple-engine-instances-cascade.md's "Rate-
# limit cooldown signal" section for why.
RATE_LIMIT_FAILURE_THRESHOLD = 3
RATE_LIMIT_COOLDOWN_HOURS = 24

# Consecutive ProviderAuthError (401) failures against the SAME instance
# before it's marked auth-disabled for AUTH_COOLDOWN_HOURS. Tracked
# separately from the rate-limit counters above — a bad/revoked/disabled
# API key will never self-resolve by waiting out a cooldown the way a
# rate limit does, so mislabeling it as "rate limited" would be actively
# misleading in the UI. Same threshold/shape as the rate-limit ones
# (including BURST_DEBOUNCE_SECONDS below) since a concurrent batch
# window can just as easily fire several 401s from the same dead key
# within milliseconds of each other.
AUTH_FAILURE_THRESHOLD = 3
AUTH_COOLDOWN_HOURS = 24

# Confirmed live: a healthy Gemini account (2/15 RPM, well under quota on
# AI Studio's own dashboard) still tripped the 3-strike cooldown within
# one item's concurrent batch window — several batches fired within
# milliseconds of each other via asyncio.gather() (see translator.py's
# _CONCURRENT_PROVIDERS), each independently 429'd on a short BURST limit
# distinct from the rolling per-minute average, and each called
# record_rate_limited_failure() separately, so 3 batches in the SAME
# instant counted as 3 SEPARATE strikes — mistaking one burst event for
# sustained exhaustion. Failures landing within this window of each other
# count as ONE strike, not one per call — only failures spaced further
# apart (genuinely separate attempts, not one concurrent burst) advance
# the counter.
BURST_DEBOUNCE_SECONDS = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["config"] = json.loads(d.pop("config_json"))
    d["enabled"] = bool(d["enabled"])
    return d


def list_instances(conn: sqlite3.Connection) -> list[dict]:
    """Every instance (including separators and disabled ones), in
    sort_order — the full list for the Engines page UI."""
    rows = conn.execute(
        "SELECT * FROM engine_instances ORDER BY sort_order"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_instance(conn: sqlite3.Connection, instance_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM engine_instances WHERE id = ?", (instance_id,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def create_instance(
    conn: sqlite3.Connection,
    *,
    name: str,
    provider_type: str,
    config: dict,
    enabled: bool = True,
) -> dict:
    """New instance is appended at the end of the cascade (highest
    sort_order + 1) — reordering afterward is an explicit separate
    action via reorder_instances(), not something create needs to guess
    at."""
    now = _now()
    max_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) FROM engine_instances"
    ).fetchone()[0]
    cur = conn.execute(
        """
        INSERT INTO engine_instances
            (name, provider_type, enabled, sort_order, config_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (name, provider_type, enabled, max_order + 1, json.dumps(config), now, now),
    )
    conn.commit()
    return get_instance(conn, cur.lastrowid)


def update_instance(
    conn: sqlite3.Connection,
    instance_id: int,
    *,
    name: str | None = None,
    config: dict | None = None,
    enabled: bool | None = None,
) -> dict | None:
    """Partial update — only fields explicitly passed are changed. A
    provider-specific field left out of `config` on purpose (e.g. an API
    key field the caller didn't touch because the form left it blank to
    mean 'keep existing') is the CALLER's responsibility to merge before
    calling this — this function replaces config_json wholesale when
    `config` is given, it doesn't merge dict keys itself."""
    fields: list[str] = []
    values: list = []
    if name is not None:
        fields.append("name = ?")
        values.append(name)
    if config is not None:
        fields.append("config_json = ?")
        values.append(json.dumps(config))
    if enabled is not None:
        fields.append("enabled = ?")
        values.append(enabled)
    if not fields:
        return get_instance(conn, instance_id)
    fields.append("updated_at = ?")
    values.append(_now())
    values.append(instance_id)
    with conn:
        conn.execute(
            f"UPDATE engine_instances SET {', '.join(fields)} WHERE id = ?", values
        )
    return get_instance(conn, instance_id)


def delete_instance(conn: sqlite3.Connection, instance_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM engine_instances WHERE id = ?", (instance_id,))


def reorder_instances(conn: sqlite3.Connection, ordered_ids: list[int]) -> None:
    """Rewrites sort_order to match the given id list's position —
    ordered_ids[0] becomes sort_order 0, etc. Any existing instance NOT
    included in ordered_ids keeps its current relative position appended
    after the given ones, rather than being silently dropped from the
    cascade — protects against a stale/incomplete id list from the UI
    accidentally orphaning a row outside the visible reorder."""
    with conn:
        for position, instance_id in enumerate(ordered_ids):
            conn.execute(
                "UPDATE engine_instances SET sort_order = ?, updated_at = ? WHERE id = ?",
                (position, _now(), instance_id),
            )
        remaining = conn.execute(
            "SELECT id FROM engine_instances WHERE id NOT IN ({}) ORDER BY sort_order".format(
                ",".join("?" * len(ordered_ids)) if ordered_ids else "NULL"
            ),
            ordered_ids,
        ).fetchall()
        for offset, row in enumerate(remaining):
            conn.execute(
                "UPDATE engine_instances SET sort_order = ? WHERE id = ?",
                (len(ordered_ids) + offset, row["id"]),
            )


def get_cascade(conn: sqlite3.Connection) -> list[dict]:
    """The ordered list of instances actually usable in a translation
    run: enabled, not currently rate-limited, not currently auth-disabled,
    walked in sort_order and stopping at the first separator (a
    separator's own row is excluded from the result — it's a boundary
    marker, not a usable instance). Empty list means nothing is
    configured/available at all — caller must handle that (today: the
    run can't start)."""
    rows = conn.execute(
        "SELECT * FROM engine_instances ORDER BY sort_order"
    ).fetchall()
    now = _now()
    cascade: list[dict] = []
    for row in rows:
        if row["provider_type"] == SEPARATOR_TYPE:
            break
        if not row["enabled"]:
            continue
        if row["rate_limited_until"] and row["rate_limited_until"] > now:
            continue
        if row["auth_disabled_until"] and row["auth_disabled_until"] > now:
            continue
        cascade.append(_row_to_dict(row))
    return cascade


def record_success(conn: sqlite3.Connection, instance_id: int) -> None:
    """Resets both the rate-limit and auth-failure consecutive counters —
    a successful call proves the instance is healthy again, regardless of
    how many failures of either kind preceded it. Also clears
    last_failure_at/last_auth_failure_at, so a later new failure isn't
    wrongly debounced against a burst that's long since resolved."""
    with conn:
        conn.execute(
            """
            UPDATE engine_instances
            SET consecutive_failures = 0, last_failure_at = NULL,
                consecutive_auth_failures = 0, last_auth_failure_at = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (_now(), instance_id),
        )


def record_rate_limited_failure(conn: sqlite3.Connection, instance_id: int) -> bool:
    """Increments the consecutive-failure counter; once it reaches
    RATE_LIMIT_FAILURE_THRESHOLD, sets rate_limited_until 24h out and
    resets the counter. Returns True if this call tripped the cooldown.

    Failures within BURST_DEBOUNCE_SECONDS of the last recorded one are
    treated as the SAME strike (a concurrent burst hitting a short-window
    rate limit, not sustained exhaustion) — see BURST_DEBOUNCE_SECONDS'
    comment for the live incident that motivated this. Tracked via its
    OWN `last_failure_at` column rather than reusing `updated_at`, since
    `updated_at` also gets touched by unrelated writes (a name/config
    edit, a reorder) that must NOT be mistaken for a recent failure and
    incorrectly suppress a genuinely new strike."""
    with conn:
        row = conn.execute(
            "SELECT consecutive_failures, last_failure_at FROM engine_instances WHERE id = ?",
            (instance_id,),
        ).fetchone()
        if row is None:
            return False
        now = datetime.now(timezone.utc)
        if row["last_failure_at"] is not None:
            last_failure = datetime.fromisoformat(row["last_failure_at"])
            if (now - last_failure).total_seconds() < BURST_DEBOUNCE_SECONDS:
                return False
        new_count = row["consecutive_failures"] + 1
        if new_count >= RATE_LIMIT_FAILURE_THRESHOLD:
            cooldown_until = (now + timedelta(hours=RATE_LIMIT_COOLDOWN_HOURS)).isoformat()
            conn.execute(
                """
                UPDATE engine_instances
                SET consecutive_failures = 0, rate_limited_until = ?,
                    last_failure_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (cooldown_until, now.isoformat(), _now(), instance_id),
            )
            return True
        conn.execute(
            """
            UPDATE engine_instances
            SET consecutive_failures = ?, last_failure_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_count, now.isoformat(), _now(), instance_id),
        )
        return False


def record_auth_failure(conn: sqlite3.Connection, instance_id: int) -> bool:
    """Increments the consecutive-auth-failure counter; once it reaches
    AUTH_FAILURE_THRESHOLD, sets auth_disabled_until 24h out and resets
    the counter. Returns True if this call tripped the cooldown.

    Same shape as record_rate_limited_failure — see its docstring for the
    BURST_DEBOUNCE_SECONDS reasoning, which applies identically here (a
    concurrent batch window can fire several 401s from the same dead key
    within milliseconds of each other). Kept as a fully separate counter/
    column pair rather than reusing the rate-limit ones: a bad/revoked/
    disabled API key will never self-resolve by waiting out a cooldown
    the way an actual rate limit does, so conflating the two would
    mislabel a dead key as merely "rate limited" in the UI."""
    with conn:
        row = conn.execute(
            "SELECT consecutive_auth_failures, last_auth_failure_at FROM engine_instances WHERE id = ?",
            (instance_id,),
        ).fetchone()
        if row is None:
            return False
        now = datetime.now(timezone.utc)
        if row["last_auth_failure_at"] is not None:
            last_failure = datetime.fromisoformat(row["last_auth_failure_at"])
            if (now - last_failure).total_seconds() < BURST_DEBOUNCE_SECONDS:
                return False
        new_count = row["consecutive_auth_failures"] + 1
        if new_count >= AUTH_FAILURE_THRESHOLD:
            cooldown_until = (now + timedelta(hours=AUTH_COOLDOWN_HOURS)).isoformat()
            conn.execute(
                """
                UPDATE engine_instances
                SET consecutive_auth_failures = 0, auth_disabled_until = ?,
                    last_auth_failure_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (cooldown_until, now.isoformat(), _now(), instance_id),
            )
            return True
        conn.execute(
            """
            UPDATE engine_instances
            SET consecutive_auth_failures = ?, last_auth_failure_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_count, now.isoformat(), _now(), instance_id),
        )
        return False


def clear_rate_limit(conn: sqlite3.Connection, instance_id: int) -> None:
    """Clears an early cooldown of EITHER kind (rate-limit or auth) —
    called after a manually-triggered 'test connection' succeeds, so a
    user who's fixed the underlying issue (rotated key, restarted local
    server) doesn't have to wait out the full 24h. A successful test
    proves the key/connection is good again regardless of which cooldown
    was active, so both are cleared together rather than requiring the
    caller to know which one applies."""
    with conn:
        conn.execute(
            """
            UPDATE engine_instances
            SET rate_limited_until = NULL, consecutive_failures = 0,
                last_failure_at = NULL,
                auth_disabled_until = NULL, consecutive_auth_failures = 0,
                last_auth_failure_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (_now(), instance_id),
        )


def clear_all_rate_limits(conn: sqlite3.Connection) -> int:
    """Manually clears the cooldown on EVERY currently rate-limited OR
    auth-disabled instance at once — e.g. after confirming (via the
    provider's own dashboard) that a trip was a false positive, or that
    whatever caused it has since been fixed (rotated key, restarted local
    server), without waiting per-instance for a successful Test
    Connection or the full 24h. Returns how many instances were actually
    cleared. No cron — deliberately manual-only, triggered from the Jobs
    page."""
    with conn:
        cur = conn.execute(
            """
            UPDATE engine_instances
            SET rate_limited_until = NULL, consecutive_failures = 0,
                last_failure_at = NULL,
                auth_disabled_until = NULL, consecutive_auth_failures = 0,
                last_auth_failure_at = NULL, updated_at = ?
            WHERE rate_limited_until IS NOT NULL OR auth_disabled_until IS NOT NULL
            """,
            (_now(),),
        )
        return cur.rowcount
