"""Parses translator/provider log lines out of the rotating log file
(app.logging_conf.LOG_FILE) into structured events for the History page's
Events tab — retries, fallbacks, per-call timing, watchdog restarts. This
is a durable, file-based record distinct from run_events.py (in-memory,
bounded, only drives live toast notifications) and from item_run_log (DB,
terminal outcomes only, no per-call granularity).

Each pattern is matched against one already-formatted log line
("%(asctime)s %(levelname)s [%(name)s] %(message)s") and mapped to an
EventType. Patterns are checked in order; the first match wins.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.logging_conf import LOG_FILE

EventType = Literal[
    "sending",
    "response",
    "item_done",
    "rate_limited_retry",
    "content_blocked_fallback",
    "provider_failed_fallback",
    "watchdog_timeout",
    "item_failed",
]

_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
    r"(?P<level>\w+) \[(?P<logger>[\w.]+)\] (?P<message>.*)$"
)

## Engine/fallback-engine names here used to be a fixed, always-space-free
## set (provider_type strings like "gemini", "nvidia") when \S+ was written
## — with engine_instances, a name is a free-text, user-editable instance
## label (e.g. "Gemini (main)") that CAN contain spaces/parens. Every
## engine-name capture group below uses a non-greedy .+? bounded by the
## next literal in the line instead of \S+, so a spaced name still parses
## correctly.
_PATTERNS: list[tuple[EventType, re.Pattern]] = [
    (
        "sending",
        re.compile(
            r"^Sending translate\(\) call for item (?P<item_id>\d+) "
            r"batch (?P<batch_index>\d+)/(?P<batch_total>\d+) to (?P<engine>.+?) "
            r"\((?P<chars>\d+) chars\)$"
        ),
    ),
    (
        "response",
        re.compile(
            r"^translate\(\) call for item (?P<item_id>\d+) \((?P<engine>.+?)\) "
            r"took (?P<seconds>[\d.]+)s$"
        ),
    ),
    (
        "item_done",
        re.compile(
            r"^Item (?P<item_id>\d+): all batches took (?P<seconds>[\d.]+)s total"
        ),
    ),
    (
        "rate_limited_retry",
        re.compile(
            r"^Provider (?P<engine>.+?) rate-limited/unreachable for item (?P<item_id>\d+) "
            r"\((?P<detail>.*)\); retrying once after (?P<wait>[\d.]+)s$"
        ),
    ),
    (
        "provider_failed_fallback",
        re.compile(
            r"^Provider (?P<engine>.+?) failed again for item (?P<item_id>\d+); "
            r"falling back to (?P<fallback_engine>.+)$"
        ),
    ),
    (
        "content_blocked_fallback",
        re.compile(
            r"^Provider (?P<engine>.+?) blocked content for item (?P<item_id>\d+) "
            r"\((?P<detail>.*)\); falling back to (?P<fallback_engine>.+)$"
        ),
    ),
    (
        "watchdog_timeout",
        re.compile(
            r"^Ollama request exceeded watchdog timeout \((?P<timeout>[\d.]+)s\) "
            r"with no response; force-unloading model and retrying once\.$"
        ),
    ),
    (
        "item_failed",
        re.compile(r"^Translation failed for item (?P<item_id>\d+)$"),
    ),
]


@dataclass
class LogEvent:
    id: int
    timestamp: str
    level: str
    event_type: EventType
    item_id: int | None
    engine: str | None
    fallback_engine: str | None
    detail: str
    raw: str


def _parse_line(line_no: int, line: str) -> LogEvent | None:
    m = _LINE_RE.match(line.rstrip("\n"))
    if m is None:
        return None
    message = m.group("message")
    for event_type, pattern in _PATTERNS:
        pm = pattern.match(message)
        if pm is None:
            continue
        fields = pm.groupdict()
        item_id = int(fields["item_id"]) if "item_id" in fields else None
        return LogEvent(
            id=line_no,
            timestamp=m.group("ts"),
            level=m.group("level"),
            event_type=event_type,
            item_id=item_id,
            engine=fields.get("engine"),
            fallback_engine=fields.get("fallback_engine"),
            detail=message,
            raw=line.rstrip("\n"),
        )
    return None


_EVENT_SORT_COLUMNS = {
    "time": lambda e: e.id,
    "item": lambda e: (e.item_id is None, e.item_id or 0),
    "engine": lambda e: (e.engine or "").lower(),
    "type": lambda e: e.event_type,
}


def read_events(
    *,
    after_id: int = 0,
    limit: int = 200,
    item_id: int | None = None,
    event_type: EventType | None = None,
    engine: str | None = None,
    sort_by: str | None = None,
    sort_dir: str = "desc",
) -> list[LogEvent]:
    """Reads the current log file (no rotation-spanning — old rotated
    files are for disaster recovery, not the Events tab) and returns
    matched, filtered events, capped at `limit`. `after_id` is a 1-based
    line number cursor: pass the smallest id already seen to page further
    back, since chronological order = line order in a single file with no
    concurrent writers other than this process's own logger (which
    serializes through the stdlib logging lock). Defaults to newest-first
    by time; `sort_by` picks a different column from the allowlist above,
    validated the same way the DB-backed sorts are (never trust the raw
    query param)."""
    if not LOG_FILE.exists():
        return []

    matched: list[LogEvent] = []
    with LOG_FILE.open("r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, start=1):
            if after_id and line_no >= after_id:
                continue
            event = _parse_line(line_no, line)
            if event is None:
                continue
            if item_id is not None and event.item_id != item_id:
                continue
            if event_type is not None and event.event_type != event_type:
                continue
            if engine is not None:
                needle = engine.lower()
                haystack = f"{event.engine or ''} {event.fallback_engine or ''}".lower()
                if needle not in haystack:
                    continue
            matched.append(event)

    key_fn = _EVENT_SORT_COLUMNS.get(sort_by) if sort_by else None
    if key_fn is None:
        key_fn = _EVENT_SORT_COLUMNS["time"]
        sort_dir = "desc"
    matched.sort(key=key_fn, reverse=(sort_dir != "asc"))
    return matched[:limit]


def latest_line_id() -> int:
    if not LOG_FILE.exists():
        return 0
    with LOG_FILE.open("r", encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)
