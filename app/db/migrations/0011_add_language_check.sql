-- Tracks whether a completed translation's actual output language has
-- been verified to match its target_language — reassemble()'s alignment/
-- integrity checks only verify STRUCTURE (cue count, index presence),
-- not language correctness, so a well-formed response that's silently
-- still in the source language (confirmed live: gemini-3.5-flash-lite
-- echoing English back for a Catalan target) passes them undetected.
-- 'unchecked' is the default for every existing and newly-completed item;
-- flipped to 'ok'/'mismatch' by the batched language-check sweep
-- (app/engine/language_check.py), never automatically reset back to
-- 'unchecked' by a normal translation run.
ALTER TABLE items ADD COLUMN language_check_status TEXT NOT NULL DEFAULT 'unchecked'
    CHECK (language_check_status IN ('unchecked', 'ok', 'mismatch'));
ALTER TABLE items ADD COLUMN language_check_detail TEXT;

CREATE INDEX idx_items_language_check_status ON items(language_check_status);
