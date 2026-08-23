import pytest

from app.bazarr.schemas import SubtitleCue, SubtitleCueTime
from app.db import database, repository
from app.engine import manual_translation


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


def _cue(index, content, start_s, end_s):
    return SubtitleCue(
        index=index, content=content, proprietary="",
        start=SubtitleCueTime(hours=0, minutes=0, seconds=start_s, total_seconds=start_s, microseconds=0),
        end=SubtitleCueTime(hours=0, minutes=0, seconds=end_s, total_seconds=end_s, microseconds=0),
    )


class FakeClient:
    def __init__(self, source_cues):
        self._source_cues = source_cues
        self.uploaded = []

    async def get_subtitle_contents(self, path):
        return self._source_cues

    async def upload_movie_subtitle(self, radarr_id, language_code2, srt_bytes, **kwargs):
        self.uploaded.append({"radarr_id": radarr_id, "language": language_code2, "srt": srt_bytes})

    async def upload_episode_subtitle(self, series_id, episode_id, language_code2, srt_bytes, **kwargs):
        self.uploaded.append(
            {"series_id": series_id, "episode_id": episode_id, "language": language_code2, "srt": srt_bytes}
        )


def _make_item(conn, bazarr_id=1):
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=bazarr_id, series_id=None,
        title="Legacy", series_title=None, season_episode=None, target_language="ca",
    )
    return conn.execute("SELECT * FROM items WHERE bazarr_id = ?", (bazarr_id,)).fetchone()


@pytest.mark.asyncio
async def test_submit_manual_translation_uploads_and_marks_done(conn):
    item = _make_item(conn)
    source_cues = [_cue(1, "Hello there.", 1, 3), _cue(2, "How are you?", 4, 6)]
    client = FakeClient(source_cues)
    translated_text = "1\nHola.\n\n2\nCom estàs?"

    result = await manual_translation.submit_manual_translation(
        conn, client, item, "en", "/movies/Legacy.en.srt", translated_text, "claude-code",
    )

    assert result["status"] == "done"
    assert result["engine_used"] == "manual"
    assert result["model_used"] == "claude-code"
    assert len(client.uploaded) == 1
    srt_text = client.uploaded[0]["srt"].decode("utf-8")
    assert "Hola." in srt_text
    assert "Com estàs?" in srt_text
    assert "[claude-code]" in srt_text  # disclaimer carries the model tag, same as any other translation

    row = conn.execute("SELECT * FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert row["status"] == "done"
    assert row["engine_used"] == "manual"
    assert row["model_used"] == "claude-code"
    assert row["source_is_external"] == 0
    assert row["disclaimer_model_tagged"] == 1


@pytest.mark.asyncio
async def test_submit_manual_translation_rejects_unusable_response(conn):
    """A response with none of the original cues recoverable (garbage,
    or a totally different structure) fails reassemble()'s own
    MIN_RECOVERABLE_FRACTION alignment check — same guard a real
    provider's malformed output would hit — and the item is marked
    failed, not silently uploaded with mostly-untranslated content."""
    item = _make_item(conn)
    source_cues = [_cue(1, "Hello there.", 1, 3), _cue(2, "How are you?", 4, 6)]
    client = FakeClient(source_cues)
    translated_text = "not a valid response at all"

    with pytest.raises(manual_translation.ManualTranslationError):
        await manual_translation.submit_manual_translation(
            conn, client, item, "en", "/movies/Legacy.en.srt", translated_text, "claude-code",
        )

    assert client.uploaded == []
    row = conn.execute("SELECT status, error_message FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert row["status"] == "failed"
    assert row["error_message"]


@pytest.mark.asyncio
async def test_submit_manual_translation_rejects_empty_source(conn):
    item = _make_item(conn)
    client = FakeClient(source_cues=[])

    with pytest.raises(manual_translation.ManualTranslationError):
        await manual_translation.submit_manual_translation(
            conn, client, item, "en", "/movies/Legacy.en.srt", "1\nHola.", "claude-code",
        )

    assert client.uploaded == []
