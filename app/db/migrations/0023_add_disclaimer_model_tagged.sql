-- Tracks whether an already-translated item's disclaimer line has been
-- backfilled with its model name (see srt_io.disclaimer_text's
-- model_name param, added for new translations in v0.13.0). Needed
-- because the backfill is a real Bazarr write per item (re-uploading
-- the edited subtitle) — this lets the backfill job be safely re-run
-- (after an interruption, or picking up items that gained a model_used
-- since the last pass) without redundantly re-editing/re-uploading
-- files it already tagged.
ALTER TABLE items ADD COLUMN disclaimer_model_tagged INTEGER NOT NULL DEFAULT 0;
