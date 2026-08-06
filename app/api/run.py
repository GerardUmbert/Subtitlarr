import asyncio

from fastapi import APIRouter, Depends

from app import state
from app.engine import run_events

router = APIRouter(prefix="/api/run", tags=["run"])


@router.post("/now")
async def run_now(runner=Depends(state.get_runner)):
    if runner.current is not None and runner.current.active:
        return {"started": False, "reason": "A run is already in progress"}
    asyncio.create_task(runner.run_now())
    return {"started": True}


_poll_state = {"active": False, "error": None}


@router.post("/poll")
async def poll_now(runner=Depends(state.get_runner)):
    """Refreshes from Bazarr's wanted list without starting translation —
    lets the dashboard show real numbers before committing to a run."""
    if _poll_state["active"]:
        return {"started": False, "reason": "A refresh is already in progress"}
    if runner.current is not None and runner.current.active:
        return {"started": False, "reason": "A run is already in progress"}

    async def _run():
        _poll_state["active"] = True
        _poll_state["error"] = None
        try:
            await runner.poll()
        except Exception as exc:  # noqa: BLE001 - surface to the UI, don't crash the app
            _poll_state["error"] = str(exc)
        finally:
            _poll_state["active"] = False

    asyncio.create_task(_run())
    return {"started": True}


@router.get("/poll/status")
async def poll_status():
    return dict(_poll_state)


@router.get("/current")
async def get_current(runner=Depends(state.get_runner)):
    progress = runner.current
    if progress is None:
        return {"active": False}
    return {
        "active": progress.active,
        "run_id": progress.run_id,
        "triggered_by": progress.triggered_by,
        "total": progress.total,
        "processed": progress.processed,
        "failed": progress.failed,
        "rate_per_min": progress.rate_per_min,
        "eta_seconds": progress.eta_seconds,
    }


@router.get("/events/latest_id")
async def get_latest_event_id():
    """Lets a freshly loaded page seek to "now" before polling /events, so
    it doesn't replay the whole buffered backlog as toasts."""
    return {"id": run_events.latest_id()}


@router.get("/events")
async def get_run_events(since: int = 0):
    """Ephemeral per-batch events (retries, fallbacks, item failures) for
    live toast notifications — poll with `since` set to the highest `id`
    already seen; returns only newer events. In-memory only, lost on
    restart, not a substitute for item_run_log's durable history."""
    events = run_events.events_since(since)
    return {
        "events": [
            {
                "id": e.id,
                "run_id": e.run_id,
                "item_id": e.item_id,
                "batch_index": e.batch_index,
                "batch_total": e.batch_total,
                "event_type": e.event_type,
                "detail": e.detail,
            }
            for e in events
        ]
    }
