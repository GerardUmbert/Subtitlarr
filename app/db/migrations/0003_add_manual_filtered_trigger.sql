-- disable_fk: rebuilds run_history to widen its triggered_by CHECK
-- constraint; item_run_log.run_id references it, so FK checks must be off
-- for the drop/rename to not fail against existing referencing rows.
CREATE TABLE run_history_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    triggered_by    TEXT NOT NULL CHECK (triggered_by IN ('manual_full', 'scheduled', 'manual_item', 'manual_filtered')),
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    items_processed INTEGER NOT NULL DEFAULT 0,
    items_failed    INTEGER NOT NULL DEFAULT 0
);

INSERT INTO run_history_new SELECT * FROM run_history;

DROP TABLE run_history;

ALTER TABLE run_history_new RENAME TO run_history
