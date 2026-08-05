from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import state
from app.config import settings
from app.db import settings_store

router = APIRouter(prefix="/api", tags=["schedule"])


class ScheduleConfig(BaseModel):
    cron_expression: str
    age_threshold_days: int
    daily_translation_limit: int
    pause_between_items_seconds: int


@router.get("/config/schedule")
async def get_schedule_config():
    return {
        "cron_expression": settings.schedule_cron,
        "age_threshold_days": settings.age_threshold_days,
        "daily_translation_limit": settings.daily_translation_limit,
        "pause_between_items_seconds": settings.pause_between_items_seconds,
    }


@router.post("/config/schedule")
async def set_schedule_config(
    config: ScheduleConfig, scheduler=Depends(state.get_scheduler), conn=Depends(state.get_conn)
):
    if config.age_threshold_days < 0:
        raise HTTPException(status_code=422, detail="age_threshold_days must be >= 0")
    if config.daily_translation_limit < 0:
        raise HTTPException(status_code=422, detail="daily_translation_limit must be >= 0")
    if config.pause_between_items_seconds < 0:
        raise HTTPException(status_code=422, detail="pause_between_items_seconds must be >= 0")
    try:
        scheduler.reschedule(config.cron_expression)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid cron expression: {exc}") from exc

    settings.schedule_cron = config.cron_expression
    settings_store.save_one(conn, "schedule_cron", config.cron_expression)
    settings.age_threshold_days = config.age_threshold_days
    settings_store.save_one(conn, "age_threshold_days", config.age_threshold_days)
    settings.daily_translation_limit = config.daily_translation_limit
    settings_store.save_one(conn, "daily_translation_limit", config.daily_translation_limit)
    settings.pause_between_items_seconds = config.pause_between_items_seconds
    settings_store.save_one(conn, "pause_between_items_seconds", config.pause_between_items_seconds)
    return {"saved": True}


@router.get("/schedule/next-runs")
async def next_runs(scheduler=Depends(state.get_scheduler)):
    next_run = scheduler.next_run_time()
    return {"next_run": next_run.isoformat() if next_run else None}
