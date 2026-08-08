-- Widens job_events.job's CHECK constraint to include 'stale_audit' —
-- same off/rebuild/on pattern as migrations 0004, 0012, and 0015.
CREATE TABLE job_events_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job          TEXT NOT NULL CHECK (job IN ('sync_media', 'sync_subs', 'push_uploads', 'language_check', 'backup', 'stale_audit')),
    triggered_by TEXT NOT NULL CHECK (triggered_by IN ('cron', 'manual')),
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
