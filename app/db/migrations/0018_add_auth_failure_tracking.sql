-- A 401 (bad/revoked/disabled API key) is a DIFFERENT failure mode from a
-- 429 rate limit — it will never self-resolve by waiting out a cooldown
-- window, and conflating it with consecutive_failures/rate_limited_until
-- would mislabel a dead key as merely "rate limited" in the UI. Tracked
-- with its own counter/cooldown column pair, same shape as the existing
-- rate-limit ones, so the two causes stay distinguishable everywhere
-- (engine_instances_repo.get_cascade, the Engines page's disabled-state
-- messaging).
ALTER TABLE engine_instances ADD COLUMN consecutive_auth_failures INTEGER NOT NULL DEFAULT 0;
ALTER TABLE engine_instances ADD COLUMN auth_disabled_until TIMESTAMP;
ALTER TABLE engine_instances ADD COLUMN last_auth_failure_at TIMESTAMP;
