from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import state
from app.api import jobs as jobs_api
from app.config import settings
from app.db import settings_store

router = APIRouter(prefix="/api", tags=["schedule"])


class ScheduleConfig(BaseModel):
    cron_expression: str
    age_threshold_days: int
    daily_translation_limit: int
    pause_between_items_seconds: int
    queue_uploads_enabled: bool
    push_uploads_cron: str
    sync_media_cron: str
    sync_subs_cron: str
    language_check_cron: str
    backup_cron: str


@router.get("/config/schedule")
def get_schedule_config():
    return {
        "cron_expression": settings.schedule_cron,
        "age_threshold_days": settings.age_threshold_days,
        "daily_translation_limit": settings.daily_translation_limit,
        "pause_between_items_seconds": settings.pause_between_items_seconds,
        "queue_uploads_enabled": settings.queue_uploads_enabled,
        "push_uploads_cron": settings.push_uploads_cron,
        "sync_media_cron": settings.sync_media_cron,
        "sync_subs_cron": settings.sync_subs_cron,
        "language_check_cron": settings.language_check_cron,
        "backup_cron": settings.backup_cron,
        "backup_keep_count": settings.backup_keep_count,
    }


def _apply_sync_cron(scheduler, job_id: str, cron_expr: str, callback) -> None:
    """Installs, reschedules, or removes a sync job depending on whether a
    cron expression is set — these two jobs are opt-in (empty = manual-only
    via the Jobs page), unlike the main translation cron which is always
    installed."""
    if not cron_expr:
        scheduler.remove(job_id)
        return
    try:
        scheduler.reschedule(cron_expr, job_id=job_id)
    except RuntimeError:
        scheduler.install(cron_expr, callback, job_id=job_id)


@router.post("/config/schedule")
def set_schedule_config(
    config: ScheduleConfig,
    scheduler=Depends(state.get_scheduler),
    conn=Depends(state.get_conn),
    runner=Depends(state.get_runner),
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

    try:
        _apply_sync_cron(
            scheduler, "sync_media", config.sync_media_cron,
            lambda: jobs_api.cron_sync_media(runner),
        )
        _apply_sync_cron(
            scheduler, "sync_subs", config.sync_subs_cron,
            lambda: jobs_api.cron_sync_subs(runner),
        )
        _apply_sync_cron(
            scheduler, "language_check", config.language_check_cron,
            lambda: jobs_api.cron_language_check(runner),
        )
        _apply_sync_cron(
            scheduler, "push_uploads", config.push_uploads_cron,
            jobs_api.cron_push_uploads,
        )
        _apply_sync_cron(
            scheduler, "backup", config.backup_cron,
            jobs_api.cron_backup,
        )
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
    settings.queue_uploads_enabled = config.queue_uploads_enabled
    settings_store.save_one(conn, "queue_uploads_enabled", config.queue_uploads_enabled)
    settings.push_uploads_cron = config.push_uploads_cron
    settings_store.save_one(conn, "push_uploads_cron", config.push_uploads_cron)
    settings.sync_media_cron = config.sync_media_cron
    settings_store.save_one(conn, "sync_media_cron", config.sync_media_cron)
    settings.sync_subs_cron = config.sync_subs_cron
    settings_store.save_one(conn, "sync_subs_cron", config.sync_subs_cron)
    settings.language_check_cron = config.language_check_cron
    settings_store.save_one(conn, "language_check_cron", config.language_check_cron)
    settings.backup_cron = config.backup_cron
    settings_store.save_one(conn, "backup_cron", config.backup_cron)
    return {"saved": True}


@router.get("/schedule/next-runs")
def next_runs(scheduler=Depends(state.get_scheduler)):
    return {
        "next_run": _iso(scheduler.next_run_time()),
        "next_sync_media_run": _iso(scheduler.next_run_time("sync_media")),
        "next_sync_subs_run": _iso(scheduler.next_run_time("sync_subs")),
        "next_language_check_run": _iso(scheduler.next_run_time("language_check")),
        "next_push_uploads_run": _iso(scheduler.next_run_time("push_uploads")),
        "next_backup_run": _iso(scheduler.next_run_time("backup")),
    }


def _iso(dt):
    return dt.isoformat() if dt else None
