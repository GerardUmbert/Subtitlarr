-- Single-row admin credentials for app-wide login (see AUTH_PLAN.md, not
-- committed to the repo — local design doc). Seeded with username "admin"
-- and a bcrypt hash of "admin" below; must_change_password forces a real
-- password to be set before a session is ever granted (see
-- app/auth/session.py). Single row by design — this app has no
-- multi-user concept, matching Sonarr/Radarr/Bazarr's own single-admin
-- auth model.
CREATE TABLE admin_credentials (
    id                   INTEGER PRIMARY KEY CHECK (id = 1),
    username             TEXT NOT NULL,
    password_hash        TEXT NOT NULL,
    must_change_password BOOLEAN NOT NULL DEFAULT 1,
    created_at           TIMESTAMP NOT NULL,
    updated_at           TIMESTAMP NOT NULL
);
