"""Shared app-wide singletons, set up in main.py's lifespan and consumed by
the API routers via FastAPI dependency functions."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
from typing import Coroutine, TYPE_CHECKING

from app.bazarr.client import BazarrClient
from app.scheduler.cron import CronScheduler

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    # Import-time only — runner.py itself imports `app.state` (for
    # db_lock), so a real module-level import here would be circular.
    from app.engine.runner import RunController

db_conn: sqlite3.Connection | None = None
bazarr_client: BazarrClient | None = None
run_controller: RunController | None = None
cron_scheduler: CronScheduler | None = None

# The single sqlite3.Connection is shared between the asyncio event loop
# thread (the background runner, run via asyncio.create_task) and
# Starlette's worker threadpool (plain `def` route handlers) — sqlite3
# connections aren't safe for concurrent use across threads even with
# check_same_thread=False. get_conn() deliberately does NOT hold this lock
# for the request/task's whole lifetime (a handler doing several sequential
# repository calls, some inside helper functions several layers down, made
# whole-request locking impossible to reason about — nested acquisition of
# a plain, non-reentrant Lock deadlocks instantly). Instead, every
# individual blocking DB call site acquires db_lock itself, right around
# just that call.
db_lock = threading.Lock()


def get_conn() -> sqlite3.Connection:
    if db_conn is None:
        raise RuntimeError("Database not initialized")
    return db_conn


def get_client() -> BazarrClient:
    if bazarr_client is None:
        raise RuntimeError("Bazarr client not initialized")
    return bazarr_client


def get_runner() -> RunController:
    if run_controller is None:
        raise RuntimeError("Run controller not initialized")
    return run_controller


def get_scheduler() -> CronScheduler:
    if cron_scheduler is None:
        raise RuntimeError("Scheduler not initialized")
    return cron_scheduler


def spawn_background_task(coro: Coroutine, *, description: str) -> asyncio.Task:
    """asyncio.create_task(coro) plus a done-callback that logs any
    exception the task raised. Every API endpoint that fires a run/job as
    fire-and-forget (returning 200 immediately, e.g. run-filtered,
    sync-media, backup) MUST go through this instead of calling
    asyncio.create_task directly.

    Without this, an uncaught exception in a fire-and-forget task is only
    ever reported by asyncio's default handler as a bare "Task exception
    was never retrieved" warning — outside the app's own logging setup,
    and only fired whenever the Task object happens to be garbage
    collected, which can be arbitrarily delayed. Confirmed live: a
    filtered translation run crashed partway through resolving items
    (one bad Bazarr response), silently killing the whole run with
    nothing in the container's logs and no error surfaced to the UI —
    the run just looked like it had done nothing, with no explanation."""
    task = asyncio.create_task(coro)

    def _log_if_failed(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.exception("Background task failed: %s", description, exc_info=exc)

    task.add_done_callback(_log_if_failed)
    return task
