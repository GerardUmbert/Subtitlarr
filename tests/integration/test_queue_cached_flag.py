import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import database, repository
from app.engine import prefetch
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    seed_conn = database.connect(db_path)
    database.apply_migrations(seed_conn)
    repository.upsert_item_seen(
        seed_conn, item_type="movie", bazarr_id=1, series_id=None,
        title="Cached Movie", series_title=None, season_episode=None,
        target_language="it",
    )
    repository.upsert_item_seen(
        seed_conn, item_type="movie", bazarr_id=2, series_id=None,
        title="Uncached Movie", series_title=None, season_episode=None,
        target_language="it",
    )
    cached_item = seed_conn.execute("SELECT id FROM items WHERE bazarr_id = 1").fetchone()
    cached_item_id = cached_item["id"]
    seed_conn.close()

    monkeypatch.setattr(settings, "db_path", db_path)
    monkeypatch.setattr(settings, "bazarr_base_url", "http://bazarr.test:6767")
    monkeypatch.setattr(settings, "bazarr_api_key", "testkey")

    # Point the scratch root at an isolated tmp dir so this test can't see
    # (or pollute) a real local cache directory.
    scratch_root = tmp_path / "scratch"
    monkeypatch.setattr(prefetch, "DEFAULT_SCRATCH_ROOT", scratch_root)
    scratch_root.mkdir(parents=True, exist_ok=True)
    (scratch_root / f"{cached_item_id}.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nCached content.\n"
    )

    with TestClient(app) as c:
        yield c


def test_list_queue_flags_items_with_a_cached_source_file(client):
    resp = client.get("/api/queue")
    assert resp.status_code == 200
    rows_by_title = {r["title"]: r for r in resp.json()["data"]}

    assert rows_by_title["Cached Movie"]["source_cached_locally"] is True
    assert rows_by_title["Uncached Movie"]["source_cached_locally"] is False
