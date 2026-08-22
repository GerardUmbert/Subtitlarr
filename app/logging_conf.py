import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import settings

# Redacts any api_secret query param value before it reaches a log handler,
# regardless of which logger the record originated from.
_API_SECRET_PATTERN = re.compile(r"(api_secret=)[^&\s\"]+")


class _RedactSecretsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _API_SECRET_PATTERN.sub(r"\1<redacted>", record.getMessage())
        record.args = ()
        return True

# Alongside the DB in the same persistent volume, so it survives restarts
# and is readable in every deployment (Docker/Unraid included) — not just
# whatever way the process happened to be launched. Rotated so the History
# page's Events tab has a bounded, predictable amount of data to parse
# rather than an ever-growing file.
LOG_FILE = Path(settings.db_path).parent / "subtitlarr.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"

# pytest sets this env var on every worker; app.main is imported by many
# tests (directly or via TestClient fixtures), and configure_logging() runs
# at import time — without this check, every test run appends fake/echo/
# fake-failing engine calls into the SAME log file the live server's
# Events/Stats tab parses, corrupting production stats with test fixtures.
_RUNNING_UNDER_PYTEST = "PYTEST_CURRENT_TEST" in os.environ or "PYTEST_VERSION" in os.environ


def configure_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    formatter = logging.Formatter(_LOG_FORMAT)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    # Attached per-handler (not to a logger) so it applies to every record
    # reaching that handler regardless of which logger originated it.
    stream_handler.addFilter(_RedactSecretsFilter())

    handlers: list[logging.Handler] = [stream_handler]
    if _RUNNING_UNDER_PYTEST:
        logging.basicConfig(level=level, handlers=handlers, force=True)
        return
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(_RedactSecretsFilter())
        handlers.append(file_handler)
    except OSError:
        # Read-only or inaccessible volume — stdout logging still works,
        # the Events tab just won't have anything to read.
        logging.getLogger(__name__).warning(
            "Could not open %s for writing — Events tab will be empty", LOG_FILE
        )

    logging.basicConfig(level=level, handlers=handlers, force=True)
