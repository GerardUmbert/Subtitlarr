-- Widens job_events.job's CHECK constraint to include
-- 'external_translate', and triggered_by to include 'api' — same
-- off/rebuild/on pattern as migrations 0004, 0012, 0015, 0016, 0019,
-- 0020, and 0024. Lets a standalone /api/external-translate job (see
-- 0025_add_external_translate_jobs.sql) show up on the History page's
-- Jobs tab like every other background job — otherwise a caller with no
-- UI access to external_translate_jobs has no way to even know a
-- translation was attempted, let alone how it went. 'api' is distinct
-- from 'cron'/'manual' since this was neither a scheduled fire nor a
-- Jobs-page button click — a third party hit the endpoint directly.
CREATE TABLE job_events_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job          TEXT NOT NULL CHECK (job IN ('sync_media', 'sync_subs', 'push_uploads', 'language_check', 'backup', 'stale_audit', 'translate', 'telemetry', 'disclaimer_backfill', 'external_translate')),
    triggered_by TEXT NOT NULL CHECK (triggered_by IN ('cron', 'manual', 'api')),
    started_at   TIMESTAMP NOT NULL,
    finished_at  TIMESTAMP,
    status       TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'failed')),
    result       TEXT,
    error        TEXT
);

INSERT INTO job_events_new SELECT * FROM job_events;

DROP TABLE job_events;

ALTER TABLE job_events_new RENAME TO job_events;

CREATE INDEX idx_job_events_started ON job_events(started_at);
