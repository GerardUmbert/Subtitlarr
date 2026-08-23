-- Set when an item is marked 'done' via the "Bazarr already has a
-- subtitle here" skip in translator.py, WITHOUT Subtitlarr ever
-- translating or verifying it — distinct from a normal 'done' item,
-- where Subtitlarr's own translation is what's actually sitting on
-- Bazarr. Confirmed live: a movie's Catalan slot held a genuinely
-- pre-existing but wrong-language (Greek) subtitle, likely downloaded by
-- Bazarr itself before Subtitlarr ever looked at it; the skip guard
-- correctly recognized it wasn't Subtitlarr's own prior output and left
-- it alone, but that also meant nothing ever verified it was actually
-- Catalan, and the item sat marked 'done' being wrong indefinitely, with
-- every retry hitting the exact same skip again.
-- Surfaced on the Queue page as a badge instead of being auto-corrected
-- (auto-verifying every one would mean an LLM call for every ordinary
-- Bazarr-downloaded subtitle Subtitlarr encounters, since the whole
-- point of the age-gate delay is that Bazarr gets first chance at every
-- item) — a manual "translate anyway" action clears it once the user
-- confirms an override is actually wanted.
ALTER TABLE items ADD COLUMN source_is_external INTEGER NOT NULL DEFAULT 0;

-- Backfill: a 'done' item with no engine_used recorded can only have
-- gotten there via the skip-and-done branch (every real translation
-- always records which engine produced it) — same signal used to
-- diagnose the "Place Beyond the Pines" case live. Retroactively flags
-- existing items that predate this column so the Queue page's badge and
-- "translate anyway" action are available for them immediately, not only
-- for items marked done AFTER this migration runs.
UPDATE items SET source_is_external = 1 WHERE status = 'done' AND engine_used IS NULL;
