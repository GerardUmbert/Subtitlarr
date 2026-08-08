"""Persists runtime-editable settings (Bazarr connection, schedule) to
SQLite's app_config table, so they survive container restarts.
Deployment-time defaults still come from env vars via app.config.Settings —
this module lets values saved through the UI override those defaults for
the lifetime of the data volume, the same way source_lang_priority already
works. Engine config lives in its OWN table (engine_instances, via
app.db.engine_instances_repo) rather than here — see
plans/multiple-engine-instances-cascade.md."""
import sqlite3

from app.config import Settings
from app.db import repository

# Keys that are safe/sensible to persist across restarts. Deliberately
# excludes things like db_path/port that only make sense as deploy-time
# config, not something the running app should rewrite.
PERSISTED_KEYS = (
    "bazarr_base_url",
    "bazarr_api_key",
    "schedule_cron",
    "age_threshold_days",
    "daily_translation_limit",
    "pause_between_items_seconds",
    "queue_uploads_enabled",
    "sync_media_cron",
    "sync_subs_cron",
    "language_check_cron",
    "backup_cron",
)


def load_into(conn: sqlite3.Connection, settings: Settings) -> None:
    """Applies any DB-persisted overrides on top of the env-var defaults
    already loaded into `settings`. Call once at startup, after settings has
    been constructed from the environment."""
    for key in PERSISTED_KEYS:
        stored = repository.get_config(conn, f"settings.{key}", default=None)
        if stored is not None:
            setattr(settings, key, stored)


def save_one(conn: sqlite3.Connection, key: str, value) -> None:
    if key not in PERSISTED_KEYS:
        raise ValueError(f"{key!r} is not a persisted setting")
    repository.set_config(conn, f"settings.{key}", value)
