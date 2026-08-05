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
    }


@router.post("/run-now")
async def run_scheduled_job_now(runner=Depends(state.get_runner)):
    """Manually fires the same age-gated job the cron runs on schedule —
    lets you trigger it immediately without waiting for the next tick."""
    if runner.current is not None and runner.current.active:
        return {"started": False, "reason": "A run is already in progress"}
    asyncio.create_task(runner.run_scheduled())
    return {"started": True}


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
