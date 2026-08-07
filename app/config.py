from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Bazarr connection
    bazarr_base_url: str = ""
    bazarr_api_key: str = ""

    # Translation engines: NOT configured here anymore. Each engine is a
    # named, independently-configured row in the engine_instances DB
    # table (see app/db/engine_instances_repo.py and
    # plans/multiple-engine-instances-cascade.md) — this replaced the old
    # single active_engine/fallback_engine + per-provider-TYPE Settings
    # fields model, since you can now have several instances of the same
    # provider type (e.g. multiple Gemini keys) in an ordered cascade.

    # Scheduling
    schedule_cron: str = "0 3 * * *"
    age_threshold_days: int = 14
    # A backlog can run into the hundreds of hours of straight LLM work —
    # without a cap, "Run now" or a scheduled run will happily grind through
    # the entire queue in one go. 0 means unlimited (opt-in, not the default
    # posture for a fresh install). Only caps full-queue/scheduled runs; a
    # forced per-item re-run always bypasses this, since that's an explicit
    # one-off request.
    daily_translation_limit: int = 100
    # A brief rest between items so a long run doesn't peg the GPU
    # non-stop for hours straight. 0 disables the pause entirely.
    pause_between_items_seconds: int = 30
    # When true, a successful translation is cached to local disk instead of
    # immediately uploaded to Bazarr — items sit as 'translated_pending_upload'
    # until a separate "push queued uploads" action sends them all in one
    # burst. Lets a NAS's disks stay asleep for the whole translation run
    # (Bazarr's own upload handling is what wakes them, not anything
    # Subtitlarr does directly) and batches that wake-up into one burst
    # whenever you choose to push, instead of once per item.
    queue_uploads_enabled: bool = False

    # Independent daily crons for the two Bazarr sync jobs — deliberately
    # separate from schedule_cron (the translation job), so wanted-list and
    # source-subtitle prefetching can run ahead of a NAS waking up for
    # translation, or on their own schedule entirely. Empty string = not
    # scheduled (manual-only via the Jobs page, the original behavior).
    sync_media_cron: str = "40 9 * * *"
    sync_subs_cron: str = "40 9 * * *"

    # Runtime
    db_path: str = "/data/subtitlarr.db"
    run_concurrency: int = 1
    log_level: str = "INFO"
    port: int = 8000


settings = Settings()
