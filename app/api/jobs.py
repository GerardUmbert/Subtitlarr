import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app import state
from app.config import settings
from app.db import repository
from app.engine import upload_queue

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
async def get_jobs(
    conn=Depends(state.get_conn), scheduler=Depends(state.get_scheduler), runner=Depends(state.get_runner)
):
    next_run = scheduler.next_run_time()
    current = runner.current
    return {
        "cron_expression": settings.schedule_cron,
        "age_threshold_days": settings.age_threshold_days,
        "daily_translation_limit": settings.daily_translation_limit,
        "queue_uploads_enabled": settings.queue_uploads_enabled,
        "pending_upload_count": repository.count_items_by_status(conn, "translated_pending_upload"),
        "next_run": next_run.isoformat() if next_run else None,
        "run_active": bool(current is not None and current.active),
        "sync_media_active": _sync_media_state["active"],
        "sync_subs_active": _sync_subs_state["active"],
        "push_uploads_active": _push_uploads_state["active"],
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


async def _run_sync_media(runner) -> None:
    _sync_media_state["active"] = True
    _sync_media_state["error"] = None
    try:
        await runner.poll()
    except Exception as exc:  # noqa: BLE001 - surface to the UI, don't crash the app
        _sync_media_state["error"] = str(exc)
    finally:
        _sync_media_state["active"] = False


async def cron_sync_media(runner) -> None:
    """Cron entry point — same guard as the on-demand endpoint, so a fire
    that lands mid-run or mid-sync is silently skipped rather than queued
    or double-started."""
    if _sync_media_state["active"] or (runner.current is not None and runner.current.active):
        return
    await _run_sync_media(runner)


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
    asyncio.create_task(_run_sync_media(runner))
    return {"started": True}


_sync_subs_state = {"active": False, "error": None, "result": None}


async def _run_sync_subs(runner) -> None:
    _sync_subs_state["active"] = True
    _sync_subs_state["error"] = None
    _sync_subs_state["result"] = None
    try:
        _sync_subs_state["result"] = await runner.warm_source_cache()
    except Exception as exc:  # noqa: BLE001 - surface to the UI, don't crash the app
        _sync_subs_state["error"] = str(exc)
    finally:
        _sync_subs_state["active"] = False


async def cron_sync_subs(runner) -> None:
    if _sync_subs_state["active"] or (runner.current is not None and runner.current.active):
        return
    await _run_sync_subs(runner)


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
    asyncio.create_task(_run_sync_subs(runner))
    return {"started": True}


_push_uploads_state = {"active": False, "error": None, "result": None}


@router.post("/push-uploads")
async def push_uploads(conn=Depends(state.get_conn), client=Depends(state.get_client)):
    """Uploads every item currently held as 'translated_pending_upload' to
    Bazarr in one pass — the deferred half of queue_uploads_enabled. Only
    meaningful when that setting is (or was) on; otherwise there's nothing
    queued to push. Deliberately NOT gated on a translation run being
    active — it only touches items that already finished translating and
    are sitting in the upload queue, which a live run never writes to
    mid-progress, so there's no real conflict to guard against."""
    if _push_uploads_state["active"]:
        return {"started": False, "reason": "A push is already in progress"}

    async def _run():
        _push_uploads_state["active"] = True
        _push_uploads_state["error"] = None
        _push_uploads_state["result"] = None
        try:
            _push_uploads_state["result"] = await upload_queue.push_pending_uploads(conn, client)
        except Exception as exc:  # noqa: BLE001 - surface to the UI, don't crash the app
            _push_uploads_state["error"] = str(exc)
        finally:
            _push_uploads_state["active"] = False

    asyncio.create_task(_run())
    return {"started": True}


@router.get("/sync-status")
async def get_sync_status():
    return {
        "sync_media": dict(_sync_media_state),
        "sync_subs": dict(_sync_subs_state),
        "push_uploads": dict(_push_uploads_state),
    }


@router.post("/close-stale-runs")
async def close_stale_runs(conn=Depends(state.get_conn), runner=Depends(state.get_runner)):
    """Closes out run_history rows left open (finished_at IS NULL) by a
    process that was killed mid-batch — NOT a destructive wipe like
    clear-database. The run and its item history stay intact, just marked
    finished instead of stuck open forever on the History page. Also runs
    automatically on every server startup; this lets it be triggered
    on-demand too."""
    if runner.current is not None and runner.current.active:
        raise HTTPException(
            status_code=409, detail="Cannot close stale runs while a run is in progress"
        )
    closed = repository.close_stale_open_runs(conn)
    return {"closed": closed}


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
