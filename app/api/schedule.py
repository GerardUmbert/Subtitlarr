import functools

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import state
from app.api import jobs as jobs_api
from app.auth.session import require_session_or_mcp_token
from app.config import Settings, settings
from app.db import settings_store

router = APIRouter(
    prefix="/api", tags=["schedule"],
    dependencies=[Depends(require_session_or_mcp_token)],
)

# Field name -> (settings attr, persisted-key) is the same string for every
# schedule field, so the one tuple below both defines "what a full reset
# touches" and lets set_schedule_config's save loop and reset_schedule_config
# share the same list instead of drifting out of sync.
SCHEDULE_FIELDS = (
    "schedule_cron",
    "age_threshold_days",
    "daily_translation_limit",
    "pause_between_items_seconds",
    "clear_rate_limits_before_scheduled_run",
    "queue_uploads_enabled",
    "push_uploads_cron",
    "sync_media_cron",
    "sync_subs_cron",
    "language_check_cron",
    "backup_cron",
    "telemetry_enabled",
)


class ScheduleConfig(BaseModel):
    cron_expression: str
    age_threshold_days: int
    daily_translation_limit: int
    pause_between_items_seconds: int
    clear_rate_limits_before_scheduled_run: bool
    queue_uploads_enabled: bool
    push_uploads_cron: str
    sync_media_cron: str
    sync_subs_cron: str
    language_check_cron: str
    backup_cron: str
    telemetry_enabled: bool


class ScheduleResetRequest(BaseModel):
    fields: list[str] | None = None


@router.get("/config/schedule")
def get_schedule_config():
    return {
        "cron_expression": settings.schedule_cron,
        "age_threshold_days": settings.age_threshold_days,
        "daily_translation_limit": settings.daily_translation_limit,
        "pause_between_items_seconds": settings.pause_between_items_seconds,
        "clear_rate_limits_before_scheduled_run": settings.clear_rate_limits_before_scheduled_run,
        "queue_uploads_enabled": settings.queue_uploads_enabled,
        "push_uploads_cron": settings.push_uploads_cron,
        "sync_media_cron": settings.sync_media_cron,
        "sync_subs_cron": settings.sync_subs_cron,
        "language_check_cron": settings.language_check_cron,
        "backup_cron": settings.backup_cron,
        "backup_keep_count": settings.backup_keep_count,
        "telemetry_enabled": settings.telemetry_enabled,
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
            functools.partial(jobs_api.cron_sync_media, runner),
        )
        _apply_sync_cron(
            scheduler, "sync_subs", config.sync_subs_cron,
            functools.partial(jobs_api.cron_sync_subs, runner),
        )
        _apply_sync_cron(
            scheduler, "language_check", config.language_check_cron,
            functools.partial(jobs_api.cron_language_check, runner),
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

    values = config.model_dump()
    values["schedule_cron"] = values.pop("cron_expression")
    for key in SCHEDULE_FIELDS:
        setattr(settings, key, values[key])
        with state.db_lock:
            settings_store.save_one(conn, key, values[key])
    return {"saved": True}


@router.post("/config/schedule/reset")
def reset_schedule_config(
    request: ScheduleResetRequest,
    scheduler=Depends(state.get_scheduler),
    conn=Depends(state.get_conn),
    runner=Depends(state.get_runner),
):
    """Resets the given schedule fields (or all of them, if none named) back
    to their built-in defaults. Field names match ScheduleConfig, except the
    translation cron is "cron_expression" here too, for symmetry with
    GET/POST /api/config/schedule."""
    reset_keys = set(SCHEDULE_FIELDS)
    if request.fields:
        requested = {"schedule_cron" if f == "cron_expression" else f for f in request.fields}
        unknown = requested - set(SCHEDULE_FIELDS)
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown schedule field(s): {sorted(unknown)}")
        reset_keys = requested

    defaults = Settings()
    current = get_schedule_config()
    current["schedule_cron"] = current.pop("cron_expression")
    merged = {**current, **{key: getattr(defaults, key) for key in reset_keys}}

    payload = ScheduleConfig(
        cron_expression=merged["schedule_cron"],
        age_threshold_days=merged["age_threshold_days"],
        daily_translation_limit=merged["daily_translation_limit"],
        pause_between_items_seconds=merged["pause_between_items_seconds"],
        clear_rate_limits_before_scheduled_run=merged["clear_rate_limits_before_scheduled_run"],
        queue_uploads_enabled=merged["queue_uploads_enabled"],
        push_uploads_cron=merged["push_uploads_cron"],
        sync_media_cron=merged["sync_media_cron"],
        sync_subs_cron=merged["sync_subs_cron"],
        language_check_cron=merged["language_check_cron"],
        backup_cron=merged["backup_cron"],
        telemetry_enabled=merged["telemetry_enabled"],
    )
    set_schedule_config(payload, scheduler=scheduler, conn=conn, runner=runner)
    return get_schedule_config()


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
