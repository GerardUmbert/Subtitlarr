"""Shared app-wide singletons, set up in main.py's lifespan and consumed by
the API routers via FastAPI dependency functions."""
from __future__ import annotations

import sqlite3
import threading
from typing import TYPE_CHECKING

from app.bazarr.client import BazarrClient
from app.scheduler.cron import CronScheduler

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
