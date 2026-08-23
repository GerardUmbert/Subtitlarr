import pytest

from app.bazarr.schemas import MovieDetail, SubtitleCue, SubtitleCueTime, SubtitleInfo
from app.db import database, repository
from app.engine import disclaimer_backfill


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


def _disclaimer_cue(text="Subtitlarr used AI to translate this from English into Catalan. Expect occasional errors."):
    return SubtitleCue(
        index=1, content=text, proprietary="",
        start=SubtitleCueTime(hours=0, minutes=0, seconds=0, total_seconds=0, microseconds=0),
        end=SubtitleCueTime(hours=0, minutes=0, seconds=10, total_seconds=10, microseconds=0),
    )


def _dialogue_cue():
    return SubtitleCue(
        index=2, content="Bon dia.", proprietary="",
        start=SubtitleCueTime(hours=0, minutes=0, seconds=11, total_seconds=11, microseconds=0),
        end=SubtitleCueTime(hours=0, minutes=0, seconds=13, total_seconds=13, microseconds=0),
    )


class FakeClient:
    def __init__(self, subtitle_cues=None, detail=None):
        self.uploaded = []
        self._subtitle_cues = subtitle_cues
        self._detail = detail

    async def get_movie_detail(self, radarr_id):
        return self._detail

    async def get_episode_detail(self, sonarr_episode_id):
        return self._detail

    async def get_subtitle_contents(self, path):
        return self._subtitle_cues

    async def upload_movie_subtitle(self, radarr_id, language_code2, srt_bytes, **kwargs):
        self.uploaded.append({"radarr_id": radarr_id, "language": language_code2, "srt": srt_bytes})

    async def upload_episode_subtitle(self, series_id, episode_id, language_code2, srt_bytes, **kwargs):
        self.uploaded.append(
            {"series_id": series_id, "episode_id": episode_id, "language": language_code2, "srt": srt_bytes}
        )


def _make_done_item(conn, model_used="gemini-3.5-flash-lite", source_is_external=0, bazarr_id=1):
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=bazarr_id, series_id=None,
        title="Legacy", series_title=None, season_episode=None, target_language="ca",
    )
    item = conn.execute("SELECT id FROM items WHERE bazarr_id = ?", (bazarr_id,)).fetchone()
    repository.update_item_status(
        conn, item["id"], "done", source_language="en", engine_used="gemini", model_used=model_used,
        mark_completed=True,
    )
    if source_is_external:
        repository.set_source_is_external(conn, item["id"], True)
    return item["id"]


def test_get_items_for_disclaimer_model_backfill_excludes_external_and_unknown_model(conn):
    real_item_id = _make_done_item(conn, model_used="gemini-3.5-flash-lite", bazarr_id=1)
    _make_done_item(conn, model_used="gemini-3.5-flash-lite", source_is_external=1, bazarr_id=2)
    _make_done_item(conn, model_used=None, bazarr_id=3)

    items = repository.get_items_for_disclaimer_model_backfill(conn)

    assert [i["id"] for i in items] == [real_item_id]


def test_get_items_for_disclaimer_model_backfill_excludes_already_tagged(conn):
    item_id = _make_done_item(conn, model_used="gemini-3.5-flash-lite")
    repository.mark_disclaimer_model_tagged(conn, item_id)

    assert repository.get_items_for_disclaimer_model_backfill(conn) == []


@pytest.mark.asyncio
async def test_backfill_item_appends_model_and_reuploads(conn):
    item_id = _make_done_item(conn, model_used="gemini-3.5-flash-lite")
    item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()

    detail = MovieDetail(
        audio_language=None, monitored=True,
        path="/movies/Legacy.mkv", radarrId=1,
        subtitles=[
            SubtitleInfo(
                name="ca", code2="ca", code3="cat", forced=False, hi=False,
                path="/movies/Legacy.ca.srt", file_size=100, embedded_track_id=None,
            ),
        ],
        title="Legacy", sceneName=None,
    )
    client = FakeClient(subtitle_cues=[_disclaimer_cue(), _dialogue_cue()], detail=detail)

    outcome = await disclaimer_backfill.backfill_item(conn, client, item)

    assert outcome == "tagged"
    assert len(client.uploaded) == 1
    assert b"[gemini-3.5-flash-lite]" in client.uploaded[0]["srt"]
    row = conn.execute("SELECT disclaimer_model_tagged FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["disclaimer_model_tagged"] == 1


@pytest.mark.asyncio
async def test_backfill_item_skips_when_disclaimer_missing(conn):
    """An item without a recognizable Subtitlarr disclaimer cue (e.g.
    add_ai_disclaimer was off) has nothing to edit — must not guess at a
    cue to modify, and must not error, just mark tagged so it stops being
    re-selected."""
    item_id = _make_done_item(conn, model_used="gemini-3.5-flash-lite")
    item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()

    detail = MovieDetail(
        audio_language=None, monitored=True,
        path="/movies/Legacy.mkv", radarrId=1,
        subtitles=[
            SubtitleInfo(
                name="ca", code2="ca", code3="cat", forced=False, hi=False,
                path="/movies/Legacy.ca.srt", file_size=100, embedded_track_id=None,
            ),
        ],
        title="Legacy", sceneName=None,
    )
    client = FakeClient(subtitle_cues=[_dialogue_cue()], detail=detail)

    outcome = await disclaimer_backfill.backfill_item(conn, client, item)

    assert outcome == "skipped_no_disclaimer"
    assert client.uploaded == []
    row = conn.execute("SELECT disclaimer_model_tagged FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["disclaimer_model_tagged"] == 1


@pytest.mark.asyncio
async def test_backfill_item_skips_when_content_not_found(conn):
    item_id = _make_done_item(conn, model_used="gemini-3.5-flash-lite")
    item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    client = FakeClient(subtitle_cues=[], detail=None)  # no detail at all — removed from Bazarr since

    outcome = await disclaimer_backfill.backfill_item(conn, client, item)

    assert outcome == "skipped_no_content"
    assert client.uploaded == []
    row = conn.execute("SELECT disclaimer_model_tagged FROM items WHERE id = ?", (item_id,)).fetchone()
    assert row["disclaimer_model_tagged"] == 0  # left untagged so a later pass, once content exists again, retries


@pytest.mark.asyncio
async def test_backfill_item_is_idempotent_on_rerun(conn):
    """A re-run against an item already carrying the exact tag (e.g. a
    previous pass uploaded successfully but got interrupted before
    marking tagged) must not double-append "[model] [model]"."""
    item_id = _make_done_item(conn, model_used="gemini-3.5-flash-lite")
    item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()

    already_tagged_text = (
        "Subtitlarr used AI to translate this from English into Catalan. "
        "Expect occasional errors. [gemini-3.5-flash-lite]"
    )
    detail = MovieDetail(
        audio_language=None, monitored=True,
        path="/movies/Legacy.mkv", radarrId=1,
        subtitles=[
            SubtitleInfo(
                name="ca", code2="ca", code3="cat", forced=False, hi=False,
                path="/movies/Legacy.ca.srt", file_size=100, embedded_track_id=None,
            ),
        ],
        title="Legacy", sceneName=None,
    )
    client = FakeClient(subtitle_cues=[_disclaimer_cue(already_tagged_text), _dialogue_cue()], detail=detail)

    outcome = await disclaimer_backfill.backfill_item(conn, client, item)

    assert outcome == "tagged"
    assert client.uploaded == []  # nothing re-uploaded — already correct
