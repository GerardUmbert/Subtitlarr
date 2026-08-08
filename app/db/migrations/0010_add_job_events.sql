-- Persists start/finish of the non-translation jobs (Bazarr wanted-list
-- sync, source prefetch, upload push) — these had NO durable history
-- before this migration, only an ephemeral in-memory status shown live on
-- the Jobs page while running. Translation runs already have their own
-- durable record in run_history/item_run_log; this table is deliberately
-- NOT for those, to avoid two competing sources of truth for the same
-- event.
CREATE TABLE job_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job          TEXT NOT NULL CHECK (job IN ('sync_media', 'sync_subs', 'push_uploads')),
    triggered_by TEXT NOT NULL CHECK (triggered_by IN ('cron', 'manual')),
    started_at   TIMESTAMP NOT NULL,
    finished_at  TIMESTAMP,
    status       TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'failed')),
    result       TEXT,
    error        TEXT
);

CREATE INDEX idx_job_events_started ON job_events(started_at);
