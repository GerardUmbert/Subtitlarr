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
    schedule_cron: str = "10 3 * * *"
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
    # When true, a scheduled run clears every engine's rate-limit/auth
    # cooldown before it starts, same as clicking "Clear all rate limits"
    # on the Jobs page. On by default — with a daily cron, a cooldown
    # tripped mid-run one day can outlive the ~24h gap to the next run
    # (cron time landing before the cooldown's exact trip time),
    # otherwise silently starving every following run until someone
    # notices and clears it by hand. The cascade will simply re-trip a
    # genuinely still-exhausted engine's cooldown again after a few
    # failures, same as any other run.
    clear_rate_limits_before_scheduled_run: bool = True
    # When true, a successful translation is cached to local disk instead of
    # immediately uploaded to Bazarr — items sit as 'translated_pending_upload'
    # until a separate "push queued uploads" action sends them all in one
    # burst. Lets a NAS's disks stay asleep for the whole translation run
    # (Bazarr's own upload handling is what wakes them, not anything
    # Subtitlarr does directly) and batches that wake-up into one burst
    # whenever you choose to push, instead of once per item.
    queue_uploads_enabled: bool = False
    # Only meaningful when queue_uploads_enabled is (or was) on — otherwise
    # there's never anything sitting in 'translated_pending_upload' for
    # this to act on. Scheduled after language_check_cron (5:00 AM), which
    # itself runs after schedule_cron (3:10 AM) — pushing before the check
    # has run would upload items the check hasn't had a chance to catch a
    # mismatch on yet. Empty string disables it (manual push only, via the
    # Jobs page).
    push_uploads_cron: str = "15 5 * * *"

    # Independent daily crons for the two Bazarr sync jobs — deliberately
    # separate from schedule_cron (the translation job), so wanted-list and
    # source-subtitle prefetching can run ahead of a NAS waking up for
    # translation, or on their own schedule entirely. Empty string = not
    # scheduled (manual-only via the Jobs page, the original behavior).
    # Staggered a few minutes apart (and ahead of schedule_cron) so the
    # wanted-list refresh and source prefetch both land before the
    # translation run starts, instead of racing or colliding.
    sync_media_cron: str = "0 3 * * *"
    sync_subs_cron: str = "5 3 * * *"
    # Scheduled AFTER schedule_cron (currently 3:10) rather than before —
    # deliberate tradeoff: running before would let a same-night mismatch
    # get reset to 'pending' in time for THAT night's translation run to
    # retranslate it immediately, but would never audit the batch that's
    # about to run. Running after (5:00 AM, well clear of most runs'
    # duration) covers that night's own translations too, at the cost of
    # any mismatch found waiting until the NEXT night's run to be fixed.
    # Still depends on a check engine instance being configured first (see
    # language_check_instance_id in settings_store) — a fire with none
    # picked just fails harmlessly, same as any other unconfigured job.
    language_check_cron: str = "0 5 * * *"

    # Daily snapshot of the whole SQLite database to /data/backups/ (same
    # non-volatile volume as the DB itself, so it survives a container
    # recreate) — the only recovery path for a destructive mistake like
    # clear-database, which has no undo. On by default (unlike the two
    # opt-in crons above) since it's read-only against the live DB and
    # has no dependency on user configuration to be useful. Kept well
    # away from the other crons' 3 AM cluster to avoid adding load to
    # that window. Empty string disables it.
    backup_cron: str = "30 2 * * *"
    backup_keep_count: int = 20

    # Anonymous usage telemetry: a daily ping (instance id, app version, OS,
    # configured engine types, item/translation counts — no Bazarr URL, API
    # keys, filenames, or subtitle content) sent to a GA4 property this
    # project's maintainer controls. On by default, user-visible and
    # toggleable from Settings. Silently disabled regardless of the toggle
    # if telemetry_measurement_id/telemetry_api_secret aren't set (e.g.
    # building from source without the maintainer's own GA credentials).
    telemetry_enabled: bool = True
    telemetry_measurement_id: str = ""
    telemetry_api_secret: str = ""
    telemetry_cron: str = "0 4 * * *"

    # Runtime
    db_path: str = "/data/subtitlarr.db"
    run_concurrency: int = 1
    log_level: str = "INFO"
    port: int = 7777


settings = Settings()
