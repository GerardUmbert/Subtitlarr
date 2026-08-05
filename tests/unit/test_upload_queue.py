import pytest

from app.db import database, repository
from app.engine import upload_queue


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


class FakeClient:
    def __init__(self, fail_bazarr_ids: set[int] | None = None):
        self.uploaded = []
        self._fail_bazarr_ids = fail_bazarr_ids or set()

    async def upload_episode_subtitle(self, series_id, episode_id, language_code2, srt_bytes, **kwargs):
        if episode_id in self._fail_bazarr_ids:
            raise RuntimeError(f"simulated upload failure for episode {episode_id}")
        self.uploaded.append({"episode_id": episode_id, "language": language_code2, "srt": srt_bytes})

    async def upload_movie_subtitle(self, radarr_id, language_code2, srt_bytes, **kwargs):
        if radarr_id in self._fail_bazarr_ids:
            raise RuntimeError(f"simulated upload failure for movie {radarr_id}")
        self.uploaded.append({"radarr_id": radarr_id, "language": language_code2, "srt": srt_bytes})


def _seed_pending_item(conn, bazarr_id: int, title: str, item_type: str = "episode") -> int:
    repository.upsert_item_seen(
        conn, item_type=item_type, bazarr_id=bazarr_id, series_id=1 if item_type == "episode" else None,
        title=title, series_title="Show" if item_type == "episode" else None,
        season_episode="1x1" if item_type == "episode" else None, target_language="es",
    )
    item_id = conn.execute("SELECT id FROM items WHERE bazarr_id = ?", (bazarr_id,)).fetchone()["id"]
    # mark_completed=True mirrors real translate_item() behavior: translation
    # work genuinely finished when an item lands in translated_pending_upload,
    # only the upload itself is deferred — completed_at must already be set
    # here so tests can verify push_pending_uploads() doesn't clobber it.
    repository.update_item_status(
        conn, item_id, "translated_pending_upload", engine_used="fake", mark_completed=True
    )
    return item_id


@pytest.mark.asyncio
async def test_push_uploads_one_queued_episode(conn, tmp_path):
    item_id = _seed_pending_item(conn, bazarr_id=42, title="Legacy")
    queue_dir = tmp_path / "queue"
    upload_queue.save_pending_upload(queue_dir, item_id, b"1\nHola.\n")

    client = FakeClient()
    result = await upload_queue.push_pending_uploads(conn, client, queue_dir)

    assert result == {"pushed": 1, "failed": 0}
    assert len(client.uploaded) == 1
    assert client.uploaded[0]["episode_id"] == 42
    assert client.uploaded[0]["language"] == "es"

    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["status"] == "done"
    assert row["completed_at"] is not None
    assert not (queue_dir / f"{item_id}.srt").exists()  # cleaned up after success


@pytest.mark.asyncio
async def test_push_uploads_does_not_overwrite_the_original_completed_at(conn, tmp_path):
    """completed_at reflects when TRANSLATION finished, not when it was
    pushed to Bazarr — the Queue page's duration column depends on this to
    show real per-item translation time instead of the (much longer) wait
    until someone clicks 'Push queued uploads'."""
    item_id = _seed_pending_item(conn, bazarr_id=55, title="Delayed Push")
    original_completed_at = conn.execute(
        "SELECT completed_at FROM items WHERE id = ?", (item_id,)
    ).fetchone()["completed_at"]
    assert original_completed_at is not None

    queue_dir = tmp_path / "queue"
    upload_queue.save_pending_upload(queue_dir, item_id, b"1\nHola.\n")
    client = FakeClient()
    await upload_queue.push_pending_uploads(conn, client, queue_dir)

    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["status"] == "done"
    assert row["completed_at"] == original_completed_at


@pytest.mark.asyncio
async def test_push_uploads_movie(conn, tmp_path):
    item_id = _seed_pending_item(conn, bazarr_id=99, title="A Movie", item_type="movie")
    queue_dir = tmp_path / "queue"
    upload_queue.save_pending_upload(queue_dir, item_id, b"1\nHola.\n")

    client = FakeClient()
    result = await upload_queue.push_pending_uploads(conn, client, queue_dir)

    assert result == {"pushed": 1, "failed": 0}
    assert client.uploaded[0]["radarr_id"] == 99


@pytest.mark.asyncio
async def test_one_failed_upload_does_not_block_the_rest(conn, tmp_path):
    failing_id = _seed_pending_item(conn, bazarr_id=1, title="Fails")
    ok_id = _seed_pending_item(conn, bazarr_id=2, title="Succeeds")
    queue_dir = tmp_path / "queue"
    upload_queue.save_pending_upload(queue_dir, failing_id, b"1\nFail.\n")
    upload_queue.save_pending_upload(queue_dir, ok_id, b"1\nOk.\n")

    client = FakeClient(fail_bazarr_ids={1})
    result = await upload_queue.push_pending_uploads(conn, client, queue_dir)

    assert result == {"pushed": 1, "failed": 1}

    failing_row = conn.execute("SELECT * FROM items WHERE id = ?", (failing_id,)).fetchone()
    assert failing_row["status"] == "translated_pending_upload"  # left in place for retry
    assert (queue_dir / f"{failing_id}.srt").exists()  # scratch file kept

    ok_row = conn.execute("SELECT * FROM items WHERE id = ?", (ok_id,)).fetchone()
    assert ok_row["status"] == "done"


@pytest.mark.asyncio
async def test_missing_scratch_file_counts_as_failed_without_crashing(conn, tmp_path):
    item_id = _seed_pending_item(conn, bazarr_id=7, title="No file")
    queue_dir = tmp_path / "queue"  # never written to — no {item_id}.srt exists

    client = FakeClient()
    result = await upload_queue.push_pending_uploads(conn, client, queue_dir)

    assert result == {"pushed": 0, "failed": 1}
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["status"] == "translated_pending_upload"


@pytest.mark.asyncio
async def test_push_uploads_is_a_noop_when_nothing_is_queued(conn, tmp_path):
    client = FakeClient()
    result = await upload_queue.push_pending_uploads(conn, client, tmp_path / "queue")
    assert result == {"pushed": 0, "failed": 0}
    assert client.uploaded == []
