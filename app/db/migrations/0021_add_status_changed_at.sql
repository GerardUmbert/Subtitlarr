-- last_updated is touched by upsert_item_seen on every Bazarr library sync
-- for EVERY currently-wanted item, including ones already 'done' or
-- 'failed' that were never re-attempted — it's a "row last touched by
-- anything" timestamp, not "when did this item's status last change".
-- The Queue page's duration column trusted last_updated as a stand-in for
-- "when did this failure happen", so a failed item left untouched for
-- days would get its last_updated silently refreshed by the next nightly
-- sync, making the computed duration balloon to days even though the
-- item was never retried (confirmed live: every 'failed' item's
-- last_updated was identical to the second, matching a sync run, while
-- last_attempt_at was scattered across the prior week).
-- status_changed_at is set ONLY by update_item_status, never by the sync
-- upsert, so it stays a trustworthy "when did status last actually
-- change" signal independent of unrelated sync activity.
ALTER TABLE items ADD COLUMN status_changed_at TIMESTAMP;

UPDATE items SET status_changed_at = last_updated;
