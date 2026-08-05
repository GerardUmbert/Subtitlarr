CREATE TABLE items (
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
                        CHECK (status IN ('pending', 'queued', 'translating', 'done', 'failed', 'skipped_no_source')),
    engine_used         TEXT,
    error_message       TEXT,
    first_seen_wanted   TIMESTAMP NOT NULL,
    last_updated        TIMESTAMP NOT NULL,
    last_attempt_at     TIMESTAMP,
    completed_at        TIMESTAMP,
    UNIQUE (item_type, bazarr_id, target_language)
);

CREATE INDEX idx_items_status ON items(status);
CREATE INDEX idx_items_first_seen ON items(first_seen_wanted);

CREATE TABLE run_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    triggered_by    TEXT NOT NULL CHECK (triggered_by IN ('manual_full', 'scheduled', 'manual_item')),
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    items_processed INTEGER NOT NULL DEFAULT 0,
    items_failed    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE item_run_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id       INTEGER NOT NULL REFERENCES items(id),
    run_id        INTEGER REFERENCES run_history(id),
    status        TEXT NOT NULL,
    engine_used   TEXT,
    error_message TEXT,
    created_at    TIMESTAMP NOT NULL
);

CREATE TABLE app_config (
    key         TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL,
    updated_at  TIMESTAMP NOT NULL
);
