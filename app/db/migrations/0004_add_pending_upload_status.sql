-- disable_fk: rebuilds items to widen its status CHECK constraint;
-- item_run_log.item_id references it, so FK checks must be off for the
-- drop/rename to not fail against existing referencing rows.
CREATE TABLE items_new (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type           TEXT NOT NULL CHECK (item_type IN ('episode', 'movie')),
    bazarr_id           INTEGER NOT NULL,
    series_id           INTEGER,
    title               TEXT NOT NULL,
    series_title        TEXT,
    season_episode      TEXT,
    source_language     TEXT,
    target_language     TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'queued', 'translating', 'translated_pending_upload', 'done', 'failed', 'skipped_no_source')),
    engine_used         TEXT,
    error_message       TEXT,
    first_seen_wanted   TIMESTAMP NOT NULL,
    last_updated        TIMESTAMP NOT NULL,
    last_attempt_at     TIMESTAMP,
    completed_at        TIMESTAMP,
    UNIQUE (item_type, bazarr_id, target_language)
);

INSERT INTO items_new SELECT * FROM items;

DROP TABLE items;

ALTER TABLE items_new RENAME TO items;

CREATE INDEX idx_items_status ON items(status);
CREATE INDEX idx_items_first_seen ON items(first_seen_wanted);
