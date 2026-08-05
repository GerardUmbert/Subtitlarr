"""Shared app-wide singletons, set up in main.py's lifespan and consumed by
the API routers via FastAPI dependency functions."""
import sqlite3

from app.bazarr.client import BazarrClient
from app.engine.runner import RunController
from app.scheduler.cron import CronScheduler

db_conn: sqlite3.Connection | None = None
bazarr_client: BazarrClient | None = None
run_controller: RunController | None = None
cron_scheduler: CronScheduler | None = None


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
