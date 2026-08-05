import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app import state
from app.config import settings
from app.db import repository

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
async def get_jobs(scheduler=Depends(state.get_scheduler), runner=Depends(state.get_runner)):
    next_run = scheduler.next_run_time()
    current = runner.current
    return {
        "cron_expression": settings.schedule_cron,
        "age_threshold_days": settings.age_threshold_days,
        "next_run": next_run.isoformat() if next_run else None,
        "run_active": bool(current is not None and current.active),
        "sync_media_active": _sync_media_state["active"],
        "sync_subs_active": _sync_subs_state["active"],
    }


@router.post("/run-now")
async def run_scheduled_job_now(runner=Depends(state.get_runner)):
    """Manually fires the same age-gated job the cron runs on schedule —
    lets you trigger it immediately without waiting for the next tick."""
    if runner.current is not None and runner.current.active:
        return {"started": False, "reason": "A run is already in progress"}
    asyncio.create_task(runner.run_scheduled())
    return {"started": True}


_sync_media_state = {"active": False, "error": None}


@router.post("/sync-media")
async def sync_media(runner=Depends(state.get_runner)):
    """Refreshes the wanted-list metadata from Bazarr WITHOUT starting any
    translation or fetching subtitle content — same as the Dashboard's
    'Refresh from Bazarr' button, exposed here too since Jobs is where the
    other on-demand sync actions live."""
    if _sync_media_state["active"]:
        return {"started": False, "reason": "A media sync is already in progress"}
    if runner.current is not None and runner.current.active:
        return {"started": False, "reason": "A run is already in progress"}

    async def _run():
        _sync_media_state["active"] = True
        _sync_media_state["error"] = None
        try:
            await runner.poll()
        except Exception as exc:  # noqa: BLE001 - surface to the UI, don't crash the app
            _sync_media_state["error"] = str(exc)
        finally:
            _sync_media_state["active"] = False

    asyncio.create_task(_run())
    return {"started": True}


_sync_subs_state = {"active": False, "error": None, "result": None}


@router.post("/sync-subs")
async def sync_subs(runner=Depends(state.get_runner)):
    """Resolves source language and pre-fetches subtitle CONTENT for every
    pending item into the local scratch cache — no translation, no
    uploads. Lets the NAS's disk-wake-up burst for reading source files
    happen ahead of time, independent of when an actual translation run
    starts."""
    if _sync_subs_state["active"]:
        return {"started": False, "reason": "A subtitle sync is already in progress"}
    if runner.current is not None and runner.current.active:
        return {"started": False, "reason": "A run is already in progress"}

    async def _run():
        _sync_subs_state["active"] = True
        _sync_subs_state["error"] = None
        _sync_subs_state["result"] = None
        try:
            _sync_subs_state["result"] = await runner.warm_source_cache()
        except Exception as exc:  # noqa: BLE001 - surface to the UI, don't crash the app
            _sync_subs_state["error"] = str(exc)
        finally:
            _sync_subs_state["active"] = False

    asyncio.create_task(_run())
    return {"started": True}


@router.get("/sync-status")
async def get_sync_status():
    return {
        "sync_media": dict(_sync_media_state),
        "sync_subs": dict(_sync_subs_state),
    }


@router.post("/clear-database")
async def clear_database(conn=Depends(state.get_conn), runner=Depends(state.get_runner)):
    """Wipes queue/run history (items, run_history, item_run_log) so
    mismatched or confusing local state can be reset without touching any
    saved settings — the queue re-populates fresh on the next poll/run."""
    if runner.current is not None and runner.current.active:
        raise HTTPException(
            status_code=409, detail="Cannot clear the database while a run is in progress"
        )
    result = repository.clear_queue_data(conn)
    return {"cleared": True, **result}
