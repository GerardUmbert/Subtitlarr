"""Console entry point for the `subtitlarr` command installed in the
Docker image (see bin/subtitlarr) — an operator's recovery tool for when
the app-wide login (app/auth/session.py) locks them out, run via
`docker exec -it <container> subtitlarr --reset-admin` with no running
app process involved. Opens the same sqlite file the app itself uses
(app.config.settings.db_path, i.e. DB_PATH) directly, rather than going
through the HTTP API, since a lockout is exactly the situation where the
API can't be trusted to answer."""
import argparse
import sys

from app.auth import session as auth_session
from app.config import settings
from app.db import database, repository


def reset_admin() -> None:
    conn = database.connect(settings.db_path)
    try:
        database.apply_migrations(conn)
        creds = repository.get_admin_credentials(conn)
        password_hash = auth_session.hash_password(auth_session.DEFAULT_PASSWORD)
        if creds is None:
            repository.create_admin_credentials(
                conn, auth_session.DEFAULT_USERNAME, password_hash, must_change_password=True,
            )
        else:
            repository.update_admin_username(conn, auth_session.DEFAULT_USERNAME)
            repository.update_admin_password(conn, password_hash, must_change_password=True)
    finally:
        conn.close()
    print(
        f"Admin credentials reset — username {auth_session.DEFAULT_USERNAME!r}, "
        f"password {auth_session.DEFAULT_PASSWORD!r}. You'll be asked to set a new "
        "password on next login."
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="subtitlarr")
    parser.add_argument(
        "--reset-admin", action="store_true",
        help="Reset the admin username/password back to defaults (admin/admin) if you're locked out.",
    )
    args = parser.parse_args()

    if args.reset_admin:
        reset_admin()
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
