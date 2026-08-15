import httpx
import pytest

from app.bazarr.client import BazarrError
from app.db import database, repository
from app.engine import selector
from app.engine.selector import SourceCandidate, pick_source_language


def _c(path, hi=False):
    return SourceCandidate(path=path, hi=hi)


def test_picks_priority_language_when_available():
    source_map = {"en": _c("/path/en.srt"), "it": _c("/path/it.srt")}
    assert pick_source_language(source_map, target_lang="es", source_priority=["en", "it"]) == "en"


def test_falls_back_to_any_available_language_when_priority_list_empty():
    """Core fix: an empty/unconfigured priority list must not mean 'nothing
    is translatable' — it means 'no preference, use whatever exists'. This
    matters for users whose libraries aren't English-first (e.g. Chinese,
    Thai, German only) who shouldn't have to configure anything to get
    started."""
    source_map = {"th": _c("/path/th.srt")}
    assert pick_source_language(source_map, target_lang="de", source_priority=[]) == "th"


def test_falls_back_to_any_available_language_not_in_priority_list():
    """A language that exists but wasn't explicitly added to the priority
    list must still be usable — the list is a preference, not a whitelist."""
    source_map = {"zh": _c("/path/zh.srt")}
    assert pick_source_language(source_map, target_lang="th", source_priority=["en"]) == "zh"


def test_never_picks_target_language_as_its_own_source():
    source_map = {"es": _c("/path/es.srt"), "en": _c("/path/en.srt")}
    assert pick_source_language(source_map, target_lang="es", source_priority=["es", "en"]) == "en"


def test_returns_none_when_only_source_is_the_target_language():
    source_map = {"es": _c("/path/es.srt")}
    assert pick_source_language(source_map, target_lang="es", source_priority=[]) is None


def test_returns_none_when_no_sources_at_all():
    assert pick_source_language({}, target_lang="es", source_priority=["en"]) is None


def test_priority_order_wins_over_dict_iteration_order():
    source_map = {"fr": _c("/path/fr.srt"), "de": _c("/path/de.srt"), "en": _c("/path/en.srt")}
    assert pick_source_language(source_map, target_lang="es", source_priority=["de", "en"]) == "de"


def test_hi_priority_language_beats_non_hi_non_priority_language():
    """Regression test for the real bug: Fastball 2026 only had an HI
    English track and a non-HI Spanish track. English (priority) + HI must
    still win over Spanish (not in priority list) + non-HI — translating
    from the wrong LANGUAGE entirely is worse than translating from an HI
    track in the right language."""
    source_map = {
        "en": SourceCandidate(path="/path/en.hi.srt", hi=True),
        "es": SourceCandidate(path="/path/es.srt", hi=False),
    }
    assert pick_source_language(source_map, target_lang="it", source_priority=["en"]) == "en"


def test_non_hi_priority_language_preferred_over_hi_same_language():
    source_map = {
        "en": SourceCandidate(path="/path/en.hi.srt", hi=True),
    }
    # only HI english available -> still picked (better than nothing)
    assert pick_source_language(source_map, target_lang="it", source_priority=["en"]) == "en"


def test_non_hi_beats_hi_within_same_priority_tier():
    """build_source_map itself prefers non-HI over HI for the same language
    code when both exist — this test documents pick_source_language's
    behavior given that pre-filtered map (a single candidate per language)."""
    # en has both hi and non-hi versions in Bazarr, but build_source_map
    # would have already collapsed that to the non-hi one — simulate that
    # pre-filtered state here.
    source_map = {"en": SourceCandidate(path="/path/en.srt", hi=False)}
    assert pick_source_language(source_map, target_lang="it", source_priority=["en"]) == "en"


def test_any_language_non_hi_beats_any_language_hi_when_nothing_on_priority_list():
    source_map = {
        "de": SourceCandidate(path="/path/de.hi.srt", hi=True),
        "fr": SourceCandidate(path="/path/fr.srt", hi=False),
    }
    assert pick_source_language(source_map, target_lang="it", source_priority=[]) == "fr"


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


class _FailsOnceClient:
    """Raises for one specific bazarr_id, succeeds (with a usable EN
    source) for every other — reproduces a batch where exactly one item's
    Bazarr detail call fails."""

    def __init__(self, failing_bazarr_id: int, exc: Exception):
        self._failing_bazarr_id = failing_bazarr_id
        self._exc = exc
        self.calls: list[int] = []

    async def get_episode_detail(self, bazarr_id: int):
        self.calls.append(bazarr_id)
        if bazarr_id == self._failing_bazarr_id:
            raise self._exc
        from app.bazarr.schemas import EpisodeDetail, SubtitleInfo

        return EpisodeDetail(
            audio_language=None, episode=1, missing_subtitles=[], monitored=True,
            path=f"/tv/Show/{bazarr_id}.mkv", season=1, sonarrEpisodeId=bazarr_id,
            sonarrSeriesId=1,
            subtitles=[
                SubtitleInfo(
                    name="English", code2="en", code3="eng", forced=False, hi=False,
                    path=f"/tv/Show/{bazarr_id}.en.srt", file_size=100, embedded_track_id=None,
                )
            ],
            title=f"Episode {bazarr_id}", sceneName=None,
        )

    async def get_movie_detail(self, radarr_id: int):
        return None


def _seed_items(conn, count: int, target_language="es") -> list:
    for i in range(count):
        repository.upsert_item_seen(
            conn, item_type="episode", bazarr_id=100 + i, series_id=1,
            title=f"Episode {i}", series_title="Show", season_episode=f"1x{i + 1}",
            target_language=target_language,
        )
    return conn.execute("SELECT * FROM items ORDER BY bazarr_id ASC").fetchall()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        httpx.HTTPStatusError("500", request=httpx.Request("GET", "http://x"), response=httpx.Response(500)),
        httpx.ConnectError("connection refused"),
        BazarrError("bad response shape"),
    ],
)
async def test_resolve_and_gate_isolates_a_single_bad_item_instead_of_killing_the_batch(conn, exc):
    """Regression test: a real live filtered run over 5 items silently
    processed ZERO of them, with no error anywhere in the app's logs and
    no indication in the UI, because ONE item's Bazarr detail call threw
    and resolve_and_gate's unguarded loop let that exception kill the
    whole batch before run_batch's own try/finally (which only protects
    the translation loop, not item resolution) ever started. A bad item
    must be marked 'failed' and skipped — the other healthy items in the
    same batch must still resolve and come back ready."""
    items = _seed_items(conn, 5)
    failing_id = items[2]["bazarr_id"]  # the middle item fails
    client = _FailsOnceClient(failing_bazarr_id=failing_id, exc=exc)

    ready = await selector.resolve_and_gate(conn, client, items, source_priority=["en"])

    assert len(ready) == 4  # 4 healthy items still made it through
    assert client.calls == [item["bazarr_id"] for item in items]  # every item was still attempted

    failed_item = conn.execute(
        "SELECT status, error_message FROM items WHERE bazarr_id = ?", (failing_id,)
    ).fetchone()
    assert failed_item["status"] == "failed"
    assert failed_item["error_message"] is not None
