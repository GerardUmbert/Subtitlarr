import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def apply_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)"
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_version")}

    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for path in migration_files:
        version = int(path.name.split("_", 1)[0])
        if version in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        # A migration that rebuilds a table (SQLite has no ALTER for CHECK
        # constraints) must drop/recreate it inside the same transaction as
        # any FK-referencing tables' data staying intact — but PRAGMA
        # foreign_keys is a no-op once a transaction has started, so it must
        # be toggled OUTSIDE the `with conn:` block, before BEGIN.
        needs_fk_off = sql.lstrip().startswith("-- disable_fk")
        # Strip full-line `--` comments before splitting on ';' — the naive
        # split has no SQL-comment awareness, so a comment containing no
        # semicolon gets merged into the next real statement and breaks it.
        sql_no_comments = "\n".join(
            line for line in sql.splitlines() if not line.strip().startswith("--")
        )
        if needs_fk_off:
            conn.execute("PRAGMA foreign_keys=OFF")
        try:
            with conn:
                for stmt in (s.strip() for s in sql_no_comments.split(";") if s.strip()):
                    conn.execute(stmt)
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
        finally:
            if needs_fk_off:
                conn.execute("PRAGMA foreign_keys=ON")
