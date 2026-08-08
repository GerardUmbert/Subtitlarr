import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import state
from app.config import settings
from app.db import engine_instances_repo, repository
from app.engine import backup, language_check, stale_audit, upload_queue

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


async def _run_sync_media(runner, triggered_by: str) -> None:
    conn = state.get_conn()
    event_id = repository.start_job_event(conn, "sync_media", triggered_by)
    _sync_media_state["active"] = True
    _sync_media_state["error"] = None
    try:
        result = await runner.poll()
        repository.finish_job_event(
            conn, event_id, status="done",
            result=f"{result['episodes_seen']} episodes, {result['movies_seen']} movies seen",
        )
    except Exception as exc:  # noqa: BLE001 - surface to the UI, don't crash the app
        _sync_media_state["error"] = str(exc)
        repository.finish_job_event(conn, event_id, status="failed", error=str(exc))
    finally:
        _sync_media_state["active"] = False


async def cron_sync_media(runner) -> None:
    """Cron entry point — same guard as the on-demand endpoint, so a fire
    that lands mid-run or mid-sync is silently skipped rather than queued
    or double-started."""
    if _sync_media_state["active"] or (runner.current is not None and runner.current.active):
        return
    await _run_sync_media(runner, triggered_by="cron")


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
    asyncio.create_task(_run_sync_media(runner, triggered_by="manual"))
    return {"started": True}


_sync_subs_state = {"active": False, "error": None, "result": None}


async def _run_sync_subs(runner, triggered_by: str) -> None:
    conn = state.get_conn()
    event_id = repository.start_job_event(conn, "sync_subs", triggered_by)
    _sync_subs_state["active"] = True
    _sync_subs_state["error"] = None
    _sync_subs_state["result"] = None
    try:
        result = await runner.warm_source_cache()
        _sync_subs_state["result"] = result
        repository.finish_job_event(
            conn, event_id, status="done",
            result=f"{result['resolved']} resolved, {result['cached']} cached",
        )
    except Exception as exc:  # noqa: BLE001 - surface to the UI, don't crash the app
        _sync_subs_state["error"] = str(exc)
        repository.finish_job_event(conn, event_id, status="failed", error=str(exc))
    finally:
        _sync_subs_state["active"] = False


async def cron_sync_subs(runner) -> None:
    if _sync_subs_state["active"] or (runner.current is not None and runner.current.active):
        return
    await _run_sync_subs(runner, triggered_by="cron")


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
    asyncio.create_task(_run_sync_subs(runner, triggered_by="manual"))
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
        event_id = repository.start_job_event(conn, "push_uploads", triggered_by="manual")
        _push_uploads_state["active"] = True
        _push_uploads_state["error"] = None
        _push_uploads_state["result"] = None
        try:
            result = await upload_queue.push_pending_uploads(conn, client)
            _push_uploads_state["result"] = result
            repository.finish_job_event(
                conn, event_id, status="done",
                result=f"{result['pushed']} pushed, {result['failed']} failed, {result['reset']} reset",
            )
        except Exception as exc:  # noqa: BLE001 - surface to the UI, don't crash the app
            _push_uploads_state["error"] = str(exc)
            repository.finish_job_event(conn, event_id, status="failed", error=str(exc))
        finally:
            _push_uploads_state["active"] = False

    asyncio.create_task(_run())
    return {"started": True}


_language_check_state = {"active": False, "error": None, "result": None}
_LANGUAGE_CHECK_BATCH_SIZE = 25


async def _run_language_check(conn, client, triggered_by: str) -> None:
    """Sweeps every batch of the unchecked backlog (not just one) in a
    single call — shared by both the manual endpoint and the cron entry
    point below, same as the sync_media/sync_subs pattern."""
    event_id = repository.start_job_event(conn, "language_check", triggered_by=triggered_by)
    _language_check_state["active"] = True
    _language_check_state["error"] = None
    _language_check_state["result"] = None
    totals = {"checked": 0, "matched": 0, "mismatched": 0, "skipped": 0}
    try:
        # Keep sweeping batches until the backlog of unchecked items is
        # actually exhausted, rather than stopping after one batch — a
        # single run should clear the whole backlog, not require
        # re-triggering once per _LANGUAGE_CHECK_BATCH_SIZE items.
        #
        # Confirmed live on the NAS: a batch where EVERY item skips (no
        # usable sample text, or the response didn't include that line)
        # never changes any item's language_check_status — it's still
        # 'unchecked' afterwards — so get_items_for_language_check()
        # re-selects the SAME batch next iteration. The old exit
        # condition ("fewer items came back than requested") never
        # triggers for a full, all-skipped batch, so the loop ran
        # forever, repeatedly re-fetching identical Bazarr subtitle
        # content with zero progress (2600+ skipped, 0 checked). Track
        # whether a batch made any real progress (checked = matched +
        # mismatched, i.e. items that actually left 'unchecked') and stop
        # as soon as one doesn't, regardless of how full it was.
        while True:
            result = await language_check.run_language_check(
                conn, client, batch_size=_LANGUAGE_CHECK_BATCH_SIZE
            )
            for key in totals:
                totals[key] += result[key]
            _language_check_state["result"] = dict(totals)
            if result["checked"] == 0:
                break  # this batch made no progress — stop instead of looping forever
            if result["checked"] + result["skipped"] < _LANGUAGE_CHECK_BATCH_SIZE:
                break  # fewer items came back than asked for — backlog is empty
        repository.finish_job_event(
            conn, event_id, status="done",
            result=(
                f"{totals['checked']} checked, {totals['matched']} ok, "
                f"{totals['mismatched']} mismatch, {totals['skipped']} skipped"
            ),
        )
    except language_check.LanguageCheckError as exc:
        _language_check_state["error"] = str(exc)
        repository.finish_job_event(conn, event_id, status="failed", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - surface to the UI, don't crash the app
        _language_check_state["error"] = str(exc)
        repository.finish_job_event(conn, event_id, status="failed", error=str(exc))
    finally:
        _language_check_state["active"] = False


async def cron_language_check(runner) -> None:
    """Cron entry point — same guards as the on-demand endpoint, so a fire
    that lands mid-run or mid-check is silently skipped rather than queued
    or double-started."""
    if _language_check_state["active"] or (runner.current is not None and runner.current.active):
        return
    conn = state.get_conn()
    client = state.get_client()
    await _run_language_check(conn, client, triggered_by="cron")


@router.post("/language-check")
async def run_language_check_now(
    conn=Depends(state.get_conn), client=Depends(state.get_client), runner=Depends(state.get_runner)
):
    """Audits up to _LANGUAGE_CHECK_BATCH_SIZE not-yet-checked completed
    items' ACTUAL output language in one batched LLM call — catches a
    well-formed, correctly-indexed response that's simply still in the
    source language (confirmed live: gemini-3.5-flash-lite echoing
    English back for a Catalan target), which reassemble()'s structural
    checks can't detect. Never touches the item itself, only its
    language_check_status/detail — a real mismatch needs a human decision
    (re-run with a different engine, accept as-is, etc.), not an automatic
    one, since the item may already be uploaded to Bazarr.

    Blocked while a translation run is active — unlike push_uploads (which
    touches no LLM at all), this DOES call whichever engine instance is
    configured for the check, and if that's the SAME instance a live run
    is actively translating with, the two would compete for that
    instance's rate-limit window/concurrency."""
    if _language_check_state["active"]:
        return {"started": False, "reason": "A language check is already in progress"}
    if runner.current is not None and runner.current.active:
        return {"started": False, "reason": "A translation run is already in progress"}

    asyncio.create_task(_run_language_check(conn, client, triggered_by="manual"))
    return {"started": True}


class LanguageCheckSettings(BaseModel):
    instance_id: int | None = None


@router.get("/language-check/settings")
async def get_language_check_settings(conn=Depends(state.get_conn)):
    return {
        "instance_id": repository.get_config(conn, "language_check_instance_id", default=None),
        "pending_count": repository.count_language_check_pending(conn),
    }


@router.post("/language-check/settings")
async def set_language_check_settings(req: LanguageCheckSettings, conn=Depends(state.get_conn)):
    repository.set_config(conn, "language_check_instance_id", req.instance_id)
    return {"saved": True}


_backup_state = {"active": False, "error": None, "result": None}


async def _run_backup(conn, triggered_by: str) -> None:
    event_id = repository.start_job_event(conn, "backup", triggered_by=triggered_by)
    _backup_state["active"] = True
    _backup_state["error"] = None
    _backup_state["result"] = None
    try:
        result = backup.run_backup(settings.db_path, keep_count=settings.backup_keep_count)
        _backup_state["result"] = result
        repository.finish_job_event(
            conn, event_id, status="done",
            result=f"snapshot written, {result['pruned']} old snapshot(s) pruned",
        )
    except Exception as exc:  # noqa: BLE001 - surface to the UI, don't crash the app
        _backup_state["error"] = str(exc)
        repository.finish_job_event(conn, event_id, status="failed", error=str(exc))
    finally:
        _backup_state["active"] = False


async def cron_backup() -> None:
    """Cron entry point — no run/other-job guard needed: the backup reads
    via sqlite3's own online backup API against a live connection, so it's
    safe to run concurrently with a translation run or any other job."""
    if _backup_state["active"]:
        return
    conn = state.get_conn()
    await _run_backup(conn, triggered_by="cron")


@router.post("/backup")
async def run_backup_now(conn=Depends(state.get_conn)):
    """Writes an immediate snapshot of the whole database to
    /data/backups/, same as the daily cron — the only way to recover from
    a destructive mistake (clear-database has no undo) or a bad
    migration. Safe to run anytime, including mid-translation-run."""
    if _backup_state["active"]:
        return {"started": False, "reason": "A backup is already in progress"}
    asyncio.create_task(_run_backup(conn, triggered_by="manual"))
    return {"started": True}


@router.get("/backups")
async def list_backups():
    """Every snapshot currently on disk (daily-cron and manual alike),
    newest first — the restore dropdown's data source and, since only a
    filename from this exact list is accepted back by /backups/restore,
    also its allowlist."""
    return {"data": backup.list_backups(settings.db_path)}


class RestoreRequest(BaseModel):
    filename: str


@router.post("/backups/restore")
async def restore_backup_now(
    req: RestoreRequest, conn=Depends(state.get_conn), runner=Depends(state.get_runner)
):
    """Overwrites the LIVE database's content with a chosen snapshot, in
    place, via sqlite3's backup API — the app keeps running against the
    same open connection throughout, no restart needed for the DB file
    itself. Blocked whenever ANY job is active (not just translation
    runs): restoring mid-write from any of them would race against the
    connection instead of just running concurrently, unlike a plain
    backup. A safety snapshot of the pre-restore state is taken
    automatically first, so this itself is always undoable.

    NOTE: settings.py's in-memory Settings object (Bazarr connection,
    schedule, etc.) is only re-read from app_config at startup — a
    restore fixes the DB immediately, but those in-memory values can
    keep showing stale (pre-restore) state until the process restarts.
    The UI surfaces this; callers hitting this endpoint directly should
    restart the app afterward too."""
    if runner.current is not None and runner.current.active:
        raise HTTPException(status_code=409, detail="Cannot restore while a translation run is in progress")
    for name, state_dict in (
        ("media sync", _sync_media_state), ("subtitle sync", _sync_subs_state),
        ("upload push", _push_uploads_state), ("language check", _language_check_state),
        ("backup", _backup_state),
    ):
        if state_dict["active"]:
            raise HTTPException(status_code=409, detail=f"Cannot restore while a {name} is in progress")
    try:
        result = backup.restore_backup(conn, settings.db_path, req.filename)
    except backup.RestoreError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"restored": True, **result}


_stale_audit_state = {"active": False, "error": None, "result": None}


@router.post("/stale-audit")
async def run_stale_audit_now(
    conn=Depends(state.get_conn), client=Depends(state.get_client), runner=Depends(state.get_runner)
):
    """One-pass audit of every 'done' item against Bazarr's CURRENT
    subtitle state — confirmed live: items can end up marked 'done' with
    no real subtitle actually present on Bazarr for their target
    language (a stale wanted-list report, Bazarr removing the uploaded
    file since, etc. — see app.engine.stale_audit and translator.
    translate_item's own forward-looking guard against the same mistake).
    Resets any found item back to 'pending'. Cheap (one Bazarr call per
    done item, no LLM involved) but still blocked during a translation
    run to avoid resetting an item the run is actively re-checking."""
    if _stale_audit_state["active"]:
        return {"started": False, "reason": "A stale audit is already in progress"}
    if runner.current is not None and runner.current.active:
        return {"started": False, "reason": "A translation run is already in progress"}

    async def _run():
        event_id = repository.start_job_event(conn, "stale_audit", triggered_by="manual")
        _stale_audit_state["active"] = True
        _stale_audit_state["error"] = None
        _stale_audit_state["result"] = None
        try:
            result = await stale_audit.run_stale_audit(conn, client)
            _stale_audit_state["result"] = result
            repository.finish_job_event(
                conn, event_id, status="done",
                result=(
                    f"{result['checked']} checked, {result['ok']} ok, "
                    f"{result['stale']} stale (reset), {result['inconclusive']} inconclusive"
                ),
            )
        except Exception as exc:  # noqa: BLE001 - surface to the UI, don't crash the app
            _stale_audit_state["error"] = str(exc)
            repository.finish_job_event(conn, event_id, status="failed", error=str(exc))
        finally:
            _stale_audit_state["active"] = False

    asyncio.create_task(_run())
    return {"started": True}


@router.get("/sync-status")
async def get_sync_status():
    return {
        "sync_media": dict(_sync_media_state),
        "sync_subs": dict(_sync_subs_state),
        "push_uploads": dict(_push_uploads_state),
        "language_check": dict(_language_check_state),
        "backup": dict(_backup_state),
        "stale_audit": dict(_stale_audit_state),
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


@router.post("/clear-engine-rate-limits")
async def clear_engine_rate_limits(conn=Depends(state.get_conn)):
    """Manually clears the rate-limit cooldown on every currently-flagged
    engine instance at once — for when a trip turns out to be a false
    positive (e.g. a burst-limit blip, confirmed against the provider's
    own usage dashboard showing real headroom) rather than genuine
    exhaustion, without waiting per-instance for a Test Connection or the
    full 24h. Deliberately manual-only — no cron for this."""
    cleared = engine_instances_repo.clear_all_rate_limits(conn)
    return {"cleared": cleared}


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
