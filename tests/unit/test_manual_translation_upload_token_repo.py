"""Stage 5 of AUTH_PLAN.md (local, untracked design doc): a short-lived,
single-use, item-scoped token for POST /api/queue/{item_id}/manual-
translation, replacing that route's previous total lack of auth. These
tests cover the repository-level consume logic directly — see
tests/integration/test_manual_translation_upload_token.py for the actual
HTTP route behavior."""
from datetime import datetime, timedelta, timezone

import pytest

from app.db import database, repository


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


def _future(hours=1) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _past(hours=1) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def test_valid_token_consumes_successfully(conn):
    repository.create_manual_translation_upload_token(conn, "tok1", item_id=5, expires_at=_future())
    assert repository.consume_manual_translation_upload_token(conn, "tok1", item_id=5) is True


def test_token_cannot_be_reused(conn):
    repository.create_manual_translation_upload_token(conn, "tok1", item_id=5, expires_at=_future())
    assert repository.consume_manual_translation_upload_token(conn, "tok1", item_id=5) is True
    assert repository.consume_manual_translation_upload_token(conn, "tok1", item_id=5) is False


def test_expired_token_rejected(conn):
    repository.create_manual_translation_upload_token(conn, "tok1", item_id=5, expires_at=_past())
    assert repository.consume_manual_translation_upload_token(conn, "tok1", item_id=5) is False


def test_token_rejected_for_wrong_item_id(conn):
    repository.create_manual_translation_upload_token(conn, "tok1", item_id=5, expires_at=_future())
    assert repository.consume_manual_translation_upload_token(conn, "tok1", item_id=999) is False
    # and the real item_id still works afterward — a wrong-item attempt
    # must not itself burn the token.
    assert repository.consume_manual_translation_upload_token(conn, "tok1", item_id=5) is True


def test_unknown_token_rejected(conn):
    assert repository.consume_manual_translation_upload_token(conn, "never-created", item_id=5) is False
