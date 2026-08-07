CREATE TABLE engine_instances (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL,
    provider_type       TEXT NOT NULL,
    enabled             BOOLEAN NOT NULL DEFAULT 1,
    sort_order          INTEGER NOT NULL,
    config_json         TEXT NOT NULL DEFAULT '{}',
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    rate_limited_until  TIMESTAMP,
    created_at          TIMESTAMP NOT NULL,
    updated_at          TIMESTAMP NOT NULL
)
