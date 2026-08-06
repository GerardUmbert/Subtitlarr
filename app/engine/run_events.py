"""In-memory, per-batch event log for live "chunk 1/5 retrying" style
toast notifications on the frontend — distinct from item-level progress
(RunProgress) and from item_run_log (DB, terminal outcomes only). Events
are ephemeral (lost on restart) and only meant to drive a short-lived
toast, not for historical reporting."""

import itertools
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

EventType = Literal["retrying", "retry_succeeded", "fell_back", "item_failed"]

# Bounded so a very long run can't grow this unboundedly — the frontend
# only ever needs "everything since my last-seen id", and a few hundred
# events is far more than any reasonable polling interval would miss.
_MAX_EVENTS = 500

_id_counter = itertools.count(1)


@dataclass
class RunEvent:
    id: int
    run_id: int
    item_id: int
    batch_index: int
    batch_total: int
    event_type: EventType
    detail: str
    created_at: float = field(default_factory=time.time)


_events: deque[RunEvent] = deque(maxlen=_MAX_EVENTS)


def emit(
    run_id: int,
    item_id: int,
    batch_index: int,
    batch_total: int,
    event_type: EventType,
    detail: str,
) -> None:
    _events.append(
        RunEvent(
            id=next(_id_counter),
            run_id=run_id,
            item_id=item_id,
            batch_index=batch_index,
            batch_total=batch_total,
            event_type=event_type,
            detail=detail,
        )
    )


def events_since(after_id: int) -> list[RunEvent]:
    return [e for e in _events if e.id > after_id]


def latest_id() -> int:
    """Highest event id currently buffered, or 0 if none — lets a freshly
    loaded page seek to "now" instead of replaying the whole buffered
    history (up to _MAX_EVENTS) as toasts."""
    return _events[-1].id if _events else 0
