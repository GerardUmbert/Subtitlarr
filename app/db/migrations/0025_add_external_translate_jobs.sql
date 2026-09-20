-- Standalone translate-content jobs submitted by a third-party caller
-- (Bazarr or anything else) via POST /api/external-translate — deliberately
-- NOT tied to the items table: this input never came from Bazarr's wanted
-- list and there's no episode/movie identity to key it on, just raw
-- subtitle content plus a source/target language pair. Kept as its own
-- table rather than reusing run_history/item_run_log, which both assume an
-- items.id foreign key throughout.
CREATE TABLE external_translate_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'done', 'failed')),
    source_lang  TEXT NOT NULL,
    target_lang  TEXT NOT NULL,
    result_srt   TEXT,
    engine_used  TEXT,
    model_used   TEXT,
    error        TEXT,
    created_at   TIMESTAMP NOT NULL,
    finished_at  TIMESTAMP
);

CREATE INDEX idx_external_translate_jobs_created ON external_translate_jobs(created_at);
