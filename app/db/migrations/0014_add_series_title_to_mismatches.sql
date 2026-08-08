-- The Language Mismatches history had no way to identify which show/movie
-- an episode belonged to (item_title alone is just the episode name, e.g.
-- "Past" or "Play") — add series_title/season_episode captured at flag
-- time, same pattern as item_title (plain columns, not a join, so the
-- record stays meaningful after the source item is deleted).
ALTER TABLE language_mismatches ADD COLUMN series_title TEXT;
ALTER TABLE language_mismatches ADD COLUMN season_episode TEXT;
