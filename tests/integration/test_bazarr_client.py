import httpx
import pytest
import respx

from app.bazarr.client import BazarrClient, BazarrError

BASE_URL = "http://bazarr.test:6767"


@pytest.fixture
async def client():
    c = BazarrClient(base_url=BASE_URL, api_key="testkey")
    yield c
    await c.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_ping(client):
    respx.get(f"{BASE_URL}/api/system/ping").mock(
        return_value=httpx.Response(200, json={"status": "OK"})
    )
    assert await client.ping() is True


@pytest.mark.asyncio
@respx.mock
async def test_get_languages(client):
    respx.get(f"{BASE_URL}/api/system/languages").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"code2": "pt", "code3": "por", "name": "Portuguese", "enabled": True},
                {"code2": "pb", "code3": "por", "name": "Brazilian Portuguese", "enabled": True},
            ],
        )
    )
    rows = await client.get_languages()
    assert {row["code2"]: row["name"] for row in rows} == {
        "pt": "Portuguese",
        "pb": "Brazilian Portuguese",
    }


@pytest.mark.asyncio
@respx.mock
async def test_get_wanted_episodes(client):
    respx.get(f"{BASE_URL}/api/episodes/wanted").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "seriesTitle": "The Bear",
                        "episode_number": "3x7",
                        "episodeTitle": "Legacy",
                        "missing_subtitles": [
                            {"name": "Spanish", "code2": "es", "code3": "spa", "forced": False, "hi": False}
                        ],
                        "sonarrSeriesId": 1,
                        "sonarrEpisodeId": 42,
                        "sceneName": None,
                        "tags": [],
                        "seriesType": "standard",
                    }
                ],
                "total": 1,
            },
        )
    )
    items, total = await client.get_wanted_episodes()
    assert total == 1
    assert items[0].sonarrEpisodeId == 42
    assert items[0].missing_subtitles[0].code2 == "es"


@pytest.mark.asyncio
@respx.mock
async def test_get_episode_detail_with_existing_subtitle(client):
    respx.get(f"{BASE_URL}/api/episodes").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "audio_language": {"name": "English", "code2": "en", "code3": "eng"},
                        "episode": 7,
                        "missing_subtitles": [
                            {"name": "Spanish", "code2": "es", "code3": "spa", "forced": False, "hi": False}
                        ],
                        "monitored": True,
                        "path": "/tv/The Bear/S03E07.mkv",
                        "season": 3,
                        "sonarrEpisodeId": 42,
                        "sonarrSeriesId": 1,
                        "subtitles": [
                            {
                                "name": "English",
                                "code2": "en",
                                "code3": "eng",
                                "forced": False,
                                "hi": False,
                                "path": "/tv/The Bear/S03E07.en.srt",
                                "file_size": 1234,
                                "embedded_track_id": None,
                            }
                        ],
                        "title": "Legacy",
                        "sceneName": None,
                    }
                ]
            },
        )
    )
    detail = await client.get_episode_detail(42)
    assert detail is not None
    assert detail.subtitles[0].path == "/tv/The Bear/S03E07.en.srt"
    assert detail.missing_subtitles[0].code2 == "es"


@pytest.mark.asyncio
@respx.mock
async def test_get_subtitle_contents(client):
    respx.get(f"{BASE_URL}/api/subtitles/contents").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "index": 1,
                        "content": "Hello there",
                        "proprietary": "",
                        "start": {"hours": 0, "minutes": 0, "seconds": 1, "total_seconds": 1, "microseconds": 0},
                        "end": {"hours": 0, "minutes": 0, "seconds": 3, "total_seconds": 3, "microseconds": 0},
                    }
                ]
            },
        )
    )
    cues = await client.get_subtitle_contents("/tv/The Bear/S03E07.en.srt")
    assert len(cues) == 1
    assert cues[0].content == "Hello there"


@pytest.mark.asyncio
@respx.mock
async def test_get_subtitle_contents_raises_clear_error_on_non_json_response(client):
    """Seen live against a real Bazarr instance: a 200 OK with an empty (or
    otherwise non-JSON) body for one specific source file — cause
    unconfirmed, but must surface as a catchable BazarrError instead of a
    bare JSONDecodeError bubbling up uncaught."""
    respx.get(f"{BASE_URL}/api/subtitles/contents").mock(
        return_value=httpx.Response(200, text="")
    )
    with pytest.raises(BazarrError, match="non-JSON response"):
        await client.get_subtitle_contents("/tv/The Bear/S03E10.es.srt")


@pytest.mark.asyncio
@respx.mock
async def test_upload_episode_subtitle_success(client):
    route = respx.post(f"{BASE_URL}/api/episodes/subtitles").mock(
        return_value=httpx.Response(204)
    )
    await client.upload_episode_subtitle(
        series_id=1, episode_id=42, language_code2="es", srt_bytes=b"1\n00:00:01,000 --> 00:00:03,000\nHola\n"
    )
    assert route.called
    sent = route.calls[0].request
    assert b'name="episodeid"' in sent.content
    assert b'name="language"' in sent.content


@pytest.mark.asyncio
@respx.mock
async def test_upload_episode_subtitle_failure_raises(client):
    respx.post(f"{BASE_URL}/api/episodes/subtitles").mock(
        return_value=httpx.Response(409, text="Unable to save subtitles file")
    )
    with pytest.raises(BazarrError):
        await client.upload_episode_subtitle(
            series_id=1, episode_id=42, language_code2="es", srt_bytes=b"bad"
        )
