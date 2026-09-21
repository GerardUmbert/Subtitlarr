from app.auth import session as auth_session
from app.cli import reset_admin
from app.config import settings
from app.db import database, repository


def test_reset_admin_seeds_when_no_row_exists(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(settings, "db_path", db_path)

    reset_admin()

    conn = database.connect(db_path)
    try:
        creds = repository.get_admin_credentials(conn)
        assert creds["username"] == auth_session.DEFAULT_USERNAME
        assert creds["must_change_password"]
        assert auth_session.verify_login(conn, auth_session.DEFAULT_USERNAME, auth_session.DEFAULT_PASSWORD)
    finally:
        conn.close()


def test_reset_admin_restores_defaults_after_lockout(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(settings, "db_path", db_path)

    conn = database.connect(db_path)
    database.apply_migrations(conn)
    repository.create_admin_credentials(
        conn, "someone@example.com", auth_session.hash_password("forgotten-password"), must_change_password=False,
    )
    conn.close()

    reset_admin()

    conn = database.connect(db_path)
    try:
        creds = repository.get_admin_credentials(conn)
        assert creds["username"] == auth_session.DEFAULT_USERNAME
        assert creds["must_change_password"]
        assert auth_session.verify_login(conn, auth_session.DEFAULT_USERNAME, auth_session.DEFAULT_PASSWORD)
        assert not auth_session.verify_login(conn, "someone@example.com", "forgotten-password")
    finally:
        conn.close()
