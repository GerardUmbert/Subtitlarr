import pytest

from app.bazarr.schemas import LanguageInfo, MovieDetail, SubtitleInfo, WantedMovie
from app.db import database, repository
from app.engine import poller


class FakeClientWithEnglishSource:
    """A movie is wanted in Spanish and Bazarr already has an English
    subtitle for it — pick_source_language should resolve to 'en'."""

    async def iter_all_wanted_episodes(self):
        return
        yield  # pragma: no cover

    async def iter_all_wanted_movies(self):
        yield WantedMovie(
            title="Fastball",
            missing_subtitles=[LanguageInfo(name="Spanish", code2="es", code3="spa")],
            radarrId=1277,
        )

    async def get_movie_detail(self, radarr_id: int):
        return MovieDetail(
            audio_language=None, missing_subtitles=[], monitored=True,
            path="/movies/Fastball/Fastball.mkv", radarrId=radarr_id,
            subtitles=[
                SubtitleInfo(
                    name="English", code2="en", code3="eng", forced=False, hi=False,
                    path="/movies/Fastball/Fastball.en.srt", file_size=1000,
                    embedded_track_id=None,
                )
            ],
            title="Fastball", sceneName=None,
        )

    async def get_episode_detail(self, sonarr_episode_id: int):
        return None


class FakeClientWithNoSource:
    """A movie is wanted in Spanish and Bazarr has NO other-language
    subtitle at all — should be marked skipped_no_source immediately."""

    async def iter_all_wanted_episodes(self):
        return
        yield  # pragma: no cover

    async def iter_all_wanted_movies(self):
        yield WantedMovie(
            title="No Source Movie",
            missing_subtitles=[LanguageInfo(name="Spanish", code2="es", code3="spa")],
            radarrId=42,
        )

    async def get_movie_detail(self, radarr_id: int):
        return MovieDetail(
            audio_language=None, missing_subtitles=[], monitored=True,
            path="/movies/None/None.mkv", radarrId=radarr_id,
            subtitles=[], title="No Source Movie", sceneName=None,
        )

    async def get_episode_detail(self, sonarr_episode_id: int):
        return None


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


@pytest.mark.asyncio
async def test_poll_eagerly_previews_source_language(conn):
    """The Queue UI should show a real source language, not '?', for any
    item Bazarr already has a usable source subtitle for — resolved at
    poll time, before any translation has been attempted."""
    await poller.poll_once(conn, FakeClientWithEnglishSource())

    row = conn.execute(
        "SELECT * FROM items WHERE bazarr_id = 1277 AND target_language = 'es'"
    ).fetchone()
    assert row["status"] == "pending"
    assert row["source_language"] == "en"


@pytest.mark.asyncio
async def test_poll_marks_skipped_no_source_immediately_when_nothing_available(conn):
    await poller.poll_once(conn, FakeClientWithNoSource())

    row = conn.execute(
        "SELECT * FROM items WHERE bazarr_id = 42 AND target_language = 'es'"
    ).fetchone()
    assert row["status"] == "skipped_no_source"
    assert row["source_language"] is None


@pytest.mark.asyncio
async def test_preview_does_not_overwrite_an_already_done_items_source(conn):
    """A previously-completed item's recorded source_language reflects what
    was ACTUALLY used to translate it — a later poll must never overwrite
    that with a fresh preview resolution."""
    await poller.poll_once(conn, FakeClientWithEnglishSource())
    row = conn.execute(
        "SELECT id FROM items WHERE bazarr_id = 1277 AND target_language = 'es'"
    ).fetchone()
    repository.update_item_status(
        conn, row["id"], "done", source_language="fr", mark_completed=True
    )

    await poller.poll_once(conn, FakeClientWithEnglishSource())

    row = conn.execute("SELECT * FROM items WHERE id = ?", (row["id"],)).fetchone()
    assert row["status"] == "done"
    assert row["source_language"] == "fr"  # untouched, not overwritten to 'en'
