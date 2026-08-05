import pytest

from app.bazarr.schemas import MovieDetail, SubtitleInfo
from app.engine.selector import build_source_map


class FakeClient:
    def __init__(self, subtitles):
        self._subtitles = subtitles

    async def get_movie_detail(self, radarr_id):
        return MovieDetail(
            audio_language=None, missing_subtitles=[], monitored=True,
            path="/m/x.mkv", radarrId=radarr_id, subtitles=self._subtitles, title="X",
            sceneName=None,
        )

    async def get_episode_detail(self, sonarr_episode_id):
        return None


@pytest.mark.asyncio
async def test_excludes_forced_subtitles():
    client = FakeClient([
        SubtitleInfo(name="English", code2="en", code3="eng", forced=True, hi=False,
                     path="/en.forced.srt", file_size=1, embedded_track_id=None),
    ])
    result = await build_source_map(client, "movie", 1)
    assert result == {}


@pytest.mark.asyncio
async def test_keeps_hi_subtitle_when_its_the_only_option_for_that_language():
    """Regression test for the Fastball 2026 bug: an HI-only English track
    must still show up as a usable candidate (flagged hi=True), not be
    dropped outright."""
    client = FakeClient([
        SubtitleInfo(name="English", code2="en", code3="eng", forced=False, hi=True,
                     path="/en.hi.srt", file_size=1, embedded_track_id=None),
    ])
    result = await build_source_map(client, "movie", 1)
    assert "en" in result
    assert result["en"].hi is True
    assert result["en"].path == "/en.hi.srt"


@pytest.mark.asyncio
async def test_prefers_non_hi_over_hi_for_the_same_language():
    client = FakeClient([
        SubtitleInfo(name="English", code2="en", code3="eng", forced=False, hi=True,
                     path="/en.hi.srt", file_size=1, embedded_track_id=None),
        SubtitleInfo(name="English", code2="en", code3="eng", forced=False, hi=False,
                     path="/en.srt", file_size=1, embedded_track_id=None),
    ])
    result = await build_source_map(client, "movie", 1)
    assert result["en"].hi is False
    assert result["en"].path == "/en.srt"
