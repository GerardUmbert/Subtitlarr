import json

import pytest

from app.bazarr.schemas import (
    EpisodeDetail,
    LanguageInfo,
    SubtitleCue,
    SubtitleCueTime,
    SubtitleInfo,
    WantedEpisode,
)
from app.db import database, repository
from app.engine.runner import RunController
from app.providers.base import ProviderStatus, TranslationProvider


class FakeBazarrClient:
    """Stands in for BazarrClient: serves one episode with a configurable
    existing-subtitle language and no Spanish subtitle, and records what
    gets uploaded."""

    def __init__(self, wanted_episodes=None, existing_language="en", cues=None):
        self.uploaded = []
        self._wanted_episodes = wanted_episodes or []
        self._existing_language = existing_language
        self._cues = cues

    async def iter_all_wanted_episodes(self):
        for item in self._wanted_episodes:
            yield item

    async def iter_all_wanted_movies(self):
        return
        yield  # pragma: no cover - makes this an async generator with no items

    async def get_episode_detail(self, sonarr_episode_id: int) -> EpisodeDetail:
        return EpisodeDetail(
            audio_language=None,
            episode=7,
            missing_subtitles=[],
            monitored=True,
            path="/tv/The Bear/S03E07.mkv",
            season=3,
            sonarrEpisodeId=sonarr_episode_id,
            sonarrSeriesId=1,
            subtitles=[
                SubtitleInfo(
                    name=self._existing_language, code2=self._existing_language, code3="xxx",
                    forced=False, hi=False,
                    path=f"/tv/The Bear/S03E07.{self._existing_language}.srt",
                    file_size=100, embedded_track_id=None,
                )
            ],
            title="Legacy",
            sceneName=None,
        )

    async def get_movie_detail(self, radarr_id: int):
        return None

    async def get_subtitle_contents(self, subtitle_path: str) -> list[SubtitleCue]:
        assert subtitle_path == f"/tv/The Bear/S03E07.{self._existing_language}.srt"
        return self._cues or [
            SubtitleCue(
                index=1, content="Hello there.", proprietary="",
                start=SubtitleCueTime(hours=0, minutes=0, seconds=1, total_seconds=1, microseconds=0),
                end=SubtitleCueTime(hours=0, minutes=0, seconds=3, total_seconds=3, microseconds=0),
            ),
            SubtitleCue(
                index=2, content="How are you?", proprietary="",
                start=SubtitleCueTime(hours=0, minutes=0, seconds=4, total_seconds=4, microseconds=0),
                end=SubtitleCueTime(hours=0, minutes=0, seconds=6, total_seconds=6, microseconds=0),
            ),
        ]

    async def upload_episode_subtitle(self, series_id, episode_id, language_code2, srt_bytes, **kwargs):
        self.uploaded.append(
            {"series_id": series_id, "episode_id": episode_id, "language": language_code2, "srt": srt_bytes}
        )

    async def upload_movie_subtitle(self, *args, **kwargs):
        raise AssertionError("should not be called for an episode test")


class FakeProvider(TranslationProvider):
    name = "fake"

    def __init__(self):
        self.received_catalan_vegeta_insults: list[bool] = []
        self.received_european_spanish: list[bool] = []

    async def translate(
        self, dialogue_text: str, source_lang: str, target_lang: str,
        catalan_vegeta_insults: bool = False, european_spanish: bool = True,
    ) -> str:
        self.received_catalan_vegeta_insults.append(catalan_vegeta_insults)
        self.received_european_spanish.append(european_spanish)
        # trivial "translation": prefix each line to prove content flowed through
        return "1\nHola.\n\n2\n¿Cómo estás?"

    async def test_connection(self) -> ProviderStatus:
        return ProviderStatus(ok=True)


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = database.connect(db_path)
    database.apply_migrations(c)
    yield c
    c.close()


@pytest.mark.asyncio
async def test_full_translation_round_trip(conn, monkeypatch):
    repository.set_config(conn, "source_lang_priority", ["en"])
    repository.upsert_item_seen(
        conn, item_type="episode", bazarr_id=42, series_id=1,
        title="Legacy", series_title="The Bear", season_episode="3x7",
        target_language="es",
    )

    fake_client = FakeBazarrClient(
        wanted_episodes=[
            WantedEpisode(
                seriesTitle="The Bear", episode_number="3x7", episodeTitle="Legacy",
                missing_subtitles=[LanguageInfo(name="Spanish", code2="es", code3="spa")],
                sonarrSeriesId=1, sonarrEpisodeId=42,
            )
        ]
    )

    from app.config import Settings
    settings = Settings(active_engine="ollama", fallback_engine="")

    monkeypatch.setattr(
        "app.engine.runner.get_active_provider", lambda s: FakeProvider()
    )
    monkeypatch.setattr(
        "app.engine.runner.get_fallback_provider", lambda s: None
    )

    controller = RunController(conn, lambda: fake_client, settings)
    progress = await controller.run_now()

    assert progress.processed == 1
    assert progress.failed == 0
    assert len(fake_client.uploaded) == 1

    uploaded = fake_client.uploaded[0]
    assert uploaded["series_id"] == 1
    assert uploaded["episode_id"] == 42
    assert uploaded["language"] == "es"
    srt_text = uploaded["srt"].decode("utf-8")
    assert "Hola." in srt_text
    assert "¿Cómo estás?" in srt_text
    assert "00:00:01" in srt_text  # original timing preserved, not LLM-invented

    row = conn.execute("SELECT * FROM items WHERE bazarr_id = 42").fetchone()
    assert row["status"] == "done"
    assert row["source_language"] == "en"
    assert row["engine_used"] == "fake"

    log_row = conn.execute(
        "SELECT * FROM item_run_log WHERE item_id = ? ORDER BY id DESC LIMIT 1", (row["id"],)
    ).fetchone()
    snapshot = json.loads(log_row["settings_snapshot"])
    assert snapshot["engine"] == "fake"
    assert snapshot["num_ctx"] == settings.ollama_num_ctx
    assert snapshot["resolved_batch_token_budget"] > 0


@pytest.mark.asyncio
async def test_failed_translation_still_logs_engine_used(conn, monkeypatch):
    """Regression test: a failed item_run_log row previously omitted
    engine_used entirely (only set on the SUCCESS path), so
    list_run_history's "WHERE engine_used IS NOT NULL" rollup query
    silently showed primary_engine: null on the History page for any run
    that failed outright — confirmed live on a real run that failed on
    Groq. engine_used must be set from active_provider.name even when
    translation never got a usable response back."""
    repository.set_config(conn, "source_lang_priority", ["en"])
    repository.upsert_item_seen(
        conn, item_type="episode", bazarr_id=42, series_id=1,
        title="Legacy", series_title="The Bear", season_episode="3x7",
        target_language="es",
    )

    fake_client = FakeBazarrClient(
        wanted_episodes=[
            WantedEpisode(
                seriesTitle="The Bear", episode_number="3x7", episodeTitle="Legacy",
                missing_subtitles=[LanguageInfo(name="Spanish", code2="es", code3="spa")],
                sonarrSeriesId=1, sonarrEpisodeId=42,
            )
        ]
    )

    class AlwaysFailsProvider(TranslationProvider):
        name = "fake-failing"

        async def translate(self, dialogue_text, source_lang, target_lang, catalan_vegeta_insults=False, european_spanish=True):
            # Garbage response the reconciler can't align to any cue —
            # triggers TranslationAlignmentError, one of the failure
            # paths that previously dropped engine_used.
            return "not a valid numbered response"

        async def test_connection(self) -> ProviderStatus:
            return ProviderStatus(ok=True)

    from app.config import Settings
    settings = Settings(active_engine="ollama", fallback_engine="")

    monkeypatch.setattr(
        "app.engine.runner.get_active_provider", lambda s: AlwaysFailsProvider()
    )
    monkeypatch.setattr(
        "app.engine.runner.get_fallback_provider", lambda s: None
    )

    controller = RunController(conn, lambda: fake_client, settings)
    progress = await controller.run_now()

    assert progress.failed == 1

    row = conn.execute("SELECT * FROM items WHERE bazarr_id = 42").fetchone()
    assert row["status"] == "failed"

    log_row = conn.execute(
        "SELECT * FROM item_run_log WHERE item_id = ? ORDER BY id DESC LIMIT 1", (row["id"],)
    ).fetchone()
    assert log_row["status"] == "failed"
    assert log_row["engine_used"] == "fake-failing"

    runs, _ = repository.list_run_history(conn)
    assert runs[0]["primary_engine"] == "fake-failing"


@pytest.mark.asyncio
async def test_catalan_vegeta_insults_setting_reaches_the_provider(conn, monkeypatch):
    """Confirms the DB-stored catalan_vegeta_insults toggle (Language
    Rules page) actually flows from translate_item() through to the
    provider's translate() call for a Catalan target — and stays off for
    a non-Catalan item even with the setting enabled."""
    repository.set_config(conn, "source_lang_priority", ["en"])
    repository.set_config(conn, "catalan_vegeta_insults", True)
    repository.upsert_item_seen(
        conn, item_type="episode", bazarr_id=42, series_id=1,
        title="Legacy", series_title="The Bear", season_episode="3x7",
        target_language="ca",
    )

    fake_client = FakeBazarrClient(
        wanted_episodes=[
            WantedEpisode(
                seriesTitle="The Bear", episode_number="3x7", episodeTitle="Legacy",
                missing_subtitles=[LanguageInfo(name="Catalan", code2="ca", code3="cat")],
                sonarrSeriesId=1, sonarrEpisodeId=42,
            )
        ]
    )

    from app.config import Settings
    settings = Settings(active_engine="ollama", fallback_engine="")

    fake_provider = FakeProvider()
    monkeypatch.setattr("app.engine.runner.get_active_provider", lambda s: fake_provider)
    monkeypatch.setattr("app.engine.runner.get_fallback_provider", lambda s: None)

    controller = RunController(conn, lambda: fake_client, settings)
    progress = await controller.run_now()

    assert progress.processed == 1
    assert progress.failed == 0
    assert fake_provider.received_catalan_vegeta_insults == [True]


@pytest.mark.asyncio
async def test_european_spanish_setting_defaults_on_and_reaches_the_provider(conn, monkeypatch):
    """Confirms european_spanish defaults to True (unlike
    catalan_vegeta_insults, which defaults False) and flows from
    translate_item() through to the provider's translate() call WITHOUT
    the setting ever being explicitly written to the DB — a fresh install
    must get this behavior automatically, not require opting in."""
    repository.set_config(conn, "source_lang_priority", ["en"])
    repository.upsert_item_seen(
        conn, item_type="episode", bazarr_id=42, series_id=1,
        title="Legacy", series_title="The Bear", season_episode="3x7",
        target_language="es",
    )

    fake_client = FakeBazarrClient(
        wanted_episodes=[
            WantedEpisode(
                seriesTitle="The Bear", episode_number="3x7", episodeTitle="Legacy",
                missing_subtitles=[LanguageInfo(name="Spanish", code2="es", code3="spa")],
                sonarrSeriesId=1, sonarrEpisodeId=42,
            )
        ]
    )

    from app.config import Settings
    settings = Settings(active_engine="ollama", fallback_engine="")

    fake_provider = FakeProvider()
    monkeypatch.setattr("app.engine.runner.get_active_provider", lambda s: fake_provider)
    monkeypatch.setattr("app.engine.runner.get_fallback_provider", lambda s: None)

    controller = RunController(conn, lambda: fake_client, settings)
    progress = await controller.run_now()

    assert progress.processed == 1
    assert progress.failed == 0
    assert fake_provider.received_european_spanish == [True]


@pytest.mark.asyncio
async def test_european_spanish_setting_can_be_disabled(conn, monkeypatch):
    repository.set_config(conn, "source_lang_priority", ["en"])
    repository.set_config(conn, "european_spanish", False)
    repository.upsert_item_seen(
        conn, item_type="episode", bazarr_id=42, series_id=1,
        title="Legacy", series_title="The Bear", season_episode="3x7",
        target_language="es",
    )

    fake_client = FakeBazarrClient(
        wanted_episodes=[
            WantedEpisode(
                seriesTitle="The Bear", episode_number="3x7", episodeTitle="Legacy",
                missing_subtitles=[LanguageInfo(name="Spanish", code2="es", code3="spa")],
                sonarrSeriesId=1, sonarrEpisodeId=42,
            )
        ]
    )

    from app.config import Settings
    settings = Settings(active_engine="ollama", fallback_engine="")

    fake_provider = FakeProvider()
    monkeypatch.setattr("app.engine.runner.get_active_provider", lambda s: fake_provider)
    monkeypatch.setattr("app.engine.runner.get_fallback_provider", lambda s: None)

    controller = RunController(conn, lambda: fake_client, settings)
    progress = await controller.run_now()

    assert progress.processed == 1
    assert progress.failed == 0
    assert fake_provider.received_european_spanish == [False]


@pytest.mark.asyncio
async def test_queue_uploads_enabled_holds_output_instead_of_uploading(conn, monkeypatch, tmp_path):
    """With queue_uploads_enabled, a successful translation must NOT reach
    Bazarr at all — it's cached to local disk and the item marked
    translated_pending_upload, not done. Proves the deferred-upload path
    doesn't touch the network."""
    repository.set_config(conn, "source_lang_priority", ["en"])
    repository.upsert_item_seen(
        conn, item_type="episode", bazarr_id=42, series_id=1,
        title="Legacy", series_title="The Bear", season_episode="3x7",
        target_language="es",
    )

    fake_client = FakeBazarrClient(
        wanted_episodes=[
            WantedEpisode(
                seriesTitle="The Bear", episode_number="3x7", episodeTitle="Legacy",
                missing_subtitles=[LanguageInfo(name="Spanish", code2="es", code3="spa")],
                sonarrSeriesId=1, sonarrEpisodeId=42,
            )
        ]
    )

    from app.config import Settings
    settings = Settings(active_engine="ollama", fallback_engine="", queue_uploads_enabled=True)

    monkeypatch.setattr("app.engine.runner.get_active_provider", lambda s: FakeProvider())
    monkeypatch.setattr("app.engine.runner.get_fallback_provider", lambda s: None)
    monkeypatch.setattr("app.engine.upload_queue.DEFAULT_QUEUE_ROOT", tmp_path / "upload-queue")

    controller = RunController(conn, lambda: fake_client, settings)
    progress = await controller.run_now()

    assert progress.processed == 1
    assert progress.failed == 0
    assert fake_client.uploaded == []  # never reached Bazarr

    row = conn.execute("SELECT * FROM items WHERE bazarr_id = 42").fetchone()
    assert row["status"] == "translated_pending_upload"
    # completed_at IS stamped even though status isn't "done" yet — it
    # reflects when translation finished, not when it was pushed to
    # Bazarr, so the Queue page's duration column shows real per-item
    # translation time instead of "—" for every queued item.
    assert row["completed_at"] is not None
    assert row["engine_used"] == "fake"

    queued_file = tmp_path / "upload-queue" / f"{row['id']}.srt"
    assert queued_file.exists()
    assert "Hola." in queued_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_item_with_no_source_language_is_skipped(conn, monkeypatch):
    """The only genuine 'no source' case: the item's one existing subtitle
    IS the target language itself (nothing else to translate from). An
    empty/mismatched source_priority list must NOT cause a skip on its own —
    see test_selector.py for that guarantee at the unit level."""
    repository.set_config(conn, "source_lang_priority", [])
    repository.upsert_item_seen(
        conn, item_type="episode", bazarr_id=42, series_id=1,
        title="Legacy", series_title="The Bear", season_episode="3x7",
        target_language="es",
    )

    fake_client = FakeBazarrClient(
        existing_language="es",  # only existing subtitle is the target itself
        wanted_episodes=[
            WantedEpisode(
                seriesTitle="The Bear", episode_number="3x7", episodeTitle="Legacy",
                missing_subtitles=[LanguageInfo(name="Spanish", code2="es", code3="spa")],
                sonarrSeriesId=1, sonarrEpisodeId=42,
            )
        ]
    )
    from app.config import Settings
    settings = Settings(active_engine="ollama", fallback_engine="")

    monkeypatch.setattr("app.engine.runner.get_active_provider", lambda s: FakeProvider())
    monkeypatch.setattr("app.engine.runner.get_fallback_provider", lambda s: None)

    controller = RunController(conn, lambda: fake_client, settings)
    progress = await controller.run_now()

    assert progress.total == 0
    assert len(fake_client.uploaded) == 0

    row = conn.execute("SELECT * FROM items WHERE bazarr_id = 42").fetchone()
    assert row["status"] == "skipped_no_source"


class EchoProvider(TranslationProvider):
    """Translates each cue by prefixing it, so we can verify every cue
    across every batch actually got translated (not silently dropped by a
    context-window truncation, as happened in the real bug this fixes)."""

    name = "echo"

    async def translate(
        self, dialogue_text: str, source_lang: str, target_lang: str,
        catalan_vegeta_insults: bool = False, european_spanish: bool = True,
    ) -> str:
        blocks = dialogue_text.strip().split("\n\n")
        out = []
        for block in blocks:
            idx, content = block.split("\n", 1)
            out.append(f"{idx}\nTR:{content}")
        return "\n\n".join(out)

    async def test_connection(self) -> ProviderStatus:
        return ProviderStatus(ok=True)


class FakeMovieBazarrClient:
    """Minimal movie-path fake for the batching regression test — kept
    separate from FakeBazarrClient since the episode fake's shape doesn't
    map cleanly onto a movie item."""

    def __init__(self, cues):
        self._cues = cues
        self.uploaded = None

    async def iter_all_wanted_episodes(self):
        return
        yield  # pragma: no cover

    async def iter_all_wanted_movies(self):
        from app.bazarr.schemas import WantedMovie

        yield WantedMovie(
            title="Fast Forever",
            missing_subtitles=[LanguageInfo(name="Italian", code2="it", code3="ita")],
            radarrId=99,
        )

    async def get_movie_detail(self, radarr_id: int):
        from app.bazarr.schemas import MovieDetail

        return MovieDetail(
            audio_language=None, missing_subtitles=[], monitored=True,
            path="/movies/Fast Forever/Fast Forever.mkv", radarrId=radarr_id,
            subtitles=[
                SubtitleInfo(
                    name="English", code2="en", code3="eng", forced=False, hi=False,
                    path="/movies/Fast Forever/Fast Forever.en.srt",
                    file_size=1000, embedded_track_id=None,
                )
            ],
            title="Fast Forever", sceneName=None,
        )

    async def get_episode_detail(self, sonarr_episode_id: int):
        return None

    async def get_subtitle_contents(self, subtitle_path: str) -> list[SubtitleCue]:
        assert subtitle_path == "/movies/Fast Forever/Fast Forever.en.srt"
        return self._cues

    async def upload_movie_subtitle(self, radarr_id, language_code2, srt_bytes, **kwargs):
        self.uploaded = {"radarr_id": radarr_id, "language": language_code2, "srt": srt_bytes}

    async def upload_episode_subtitle(self, *args, **kwargs):
        raise AssertionError("should not be called for a movie test")


@pytest.mark.asyncio
async def test_large_subtitle_is_batched_and_fully_translated(conn, monkeypatch):
    """Regression test for the real failure observed live: a movie-length
    subtitle (1071 cues) sent as a single prompt exceeded the local model's
    context window, got silently truncated by Ollama, and the reconciler
    recovered 0/1071 cues. Batching must ensure every cue is actually sent
    to the provider and comes back translated, regardless of file length."""
    import srt as srt_lib

    n_cues = 1071
    cues = [
        SubtitleCue(
            index=i + 1, content=f"Line number {i} of dialogue.", proprietary="",
            start=SubtitleCueTime(hours=0, minutes=0, seconds=i, total_seconds=i, microseconds=0),
            end=SubtitleCueTime(hours=0, minutes=0, seconds=i + 1, total_seconds=i + 1, microseconds=0),
        )
        for i in range(n_cues)
    ]

    repository.set_config(conn, "source_lang_priority", ["en"])
    repository.upsert_item_seen(
        conn, item_type="movie", bazarr_id=99, series_id=None,
        title="Fast Forever", series_title=None, season_episode=None,
        target_language="it",
    )

    fake_client = FakeMovieBazarrClient(cues)

    from app.config import Settings
    settings = Settings(active_engine="ollama", fallback_engine="")

    monkeypatch.setattr("app.engine.runner.get_active_provider", lambda s: EchoProvider())
    monkeypatch.setattr("app.engine.runner.get_fallback_provider", lambda s: None)

    controller = RunController(conn, lambda: fake_client, settings)
    progress = await controller.run_now()

    assert progress.processed == 1
    assert progress.failed == 0
    assert fake_client.uploaded, "movie subtitle was never uploaded — translation failed"

    reparsed = list(srt_lib.parse(fake_client.uploaded["srt"].decode("utf-8")))
    # +1 for the AI disclaimer cue prepended at index 1
    assert len(reparsed) == n_cues + 1
    # every single original cue must show up translated — none silently
    # dropped by a truncated single-shot prompt, unlike the real bug
    translated_content = [c.content for c in reparsed[1:]]
    assert all(c.startswith("TR:Line number") for c in translated_content)
    assert translated_content[0] == "TR:Line number 0 of dialogue."
    assert translated_content[-1] == f"TR:Line number {n_cues - 1} of dialogue."

    row = conn.execute("SELECT * FROM items WHERE bazarr_id = 99").fetchone()
    assert row["status"] == "done"


@pytest.mark.asyncio
async def test_run_single_item_can_rerun_an_already_done_item(conn, monkeypatch):
    """Regression test: an item already marked 'done' must be re-runnable
    per-item — e.g. after a fix (like the language-code prompt ambiguity
    bug) that could have affected a previous translation."""
    repository.set_config(conn, "source_lang_priority", ["en"])
    repository.upsert_item_seen(
        conn, item_type="episode", bazarr_id=42, series_id=1,
        title="Legacy", series_title="The Bear", season_episode="3x7",
        target_language="es",
    )
    item = conn.execute("SELECT id FROM items WHERE bazarr_id = 42").fetchone()
    repository.update_item_status(
        conn, item["id"], "done", source_language="en", engine_used="ollama", mark_completed=True
    )

    fake_client = FakeBazarrClient()
    from app.config import Settings
    settings = Settings(active_engine="ollama", fallback_engine="")

    monkeypatch.setattr("app.engine.runner.get_active_provider", lambda s: FakeProvider())
    monkeypatch.setattr("app.engine.runner.get_fallback_provider", lambda s: None)

    controller = RunController(conn, lambda: fake_client, settings)
    progress = await controller.run_single_item(item["id"])

    assert progress.processed == 1
    assert progress.failed == 0
    assert len(fake_client.uploaded) == 1  # actually re-translated and re-uploaded

    row = conn.execute("SELECT * FROM items WHERE bazarr_id = 42").fetchone()
    assert row["status"] == "done"
