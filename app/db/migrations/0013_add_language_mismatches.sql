-- A durable, permanent record of every confirmed language mismatch the
-- check has ever found — separate from items.language_check_status/
-- detail, which reset back to 'unchecked'/NULL the moment the affected
-- item is reset to 'pending' for retranslation (see
-- repository.reset_item_for_language_mismatch). Without this table,
-- there was no way to answer "which items did we already send to Bazarr
-- with the wrong language" once the item's own trace of the mismatch
-- cleared on its next (hopefully correct) translation attempt.
--
-- item_id is NOT a foreign key on purpose: the flagged item can later be
-- deleted (e.g. "Clear database") while this history should still show
-- what was once caught — item_title/item_type/bazarr_id are captured as
-- plain columns at flag time so the record stays meaningful even if the
-- items row it originally pointed to is long gone.
CREATE TABLE language_mismatches (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id             INTEGER,
    item_title          TEXT NOT NULL,
    item_type           TEXT NOT NULL,
    bazarr_id           INTEGER NOT NULL,
    target_language     TEXT NOT NULL,
    detected_language   TEXT NOT NULL,
    was_uploaded        INTEGER NOT NULL,  -- 1 if the item's status was 'done' (already sent to Bazarr) at detection time, 0 if it was still 'translated_pending_upload' (caught before ever reaching Bazarr)
    detected_at         TIMESTAMP NOT NULL
);

CREATE INDEX idx_language_mismatches_detected_at ON language_mismatches(detected_at);
