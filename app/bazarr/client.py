import httpx

from app.bazarr.schemas import (
    EpisodeDetail,
    MovieDetail,
    SeriesInfo,
    SubtitleCue,
    WantedEpisode,
    WantedMovie,
)


class BazarrError(Exception):
    pass


class BazarrClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"X-API-KEY": api_key},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ping(self) -> bool:
        """Unauthenticated availability check (GET /api/system/ping)."""
        try:
            resp = await self._client.get("/api/system/ping")
            return resp.status_code == 200 and resp.json().get("status") == "OK"
        except httpx.HTTPError:
            return False

    async def test_connection(self) -> bool:
        """Authenticated check — confirms the API key works, not just that Bazarr is up."""
        try:
            resp = await self._client.get("/api/system/status")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    # ---- Wanted lists ----

    async def get_wanted_episodes(self, start: int = 0, length: int = -1) -> tuple[list[WantedEpisode], int]:
        resp = await self._client.get(
            "/api/episodes/wanted", params={"start": start, "length": length}
        )
        resp.raise_for_status()
        payload = resp.json()
        items = [WantedEpisode.model_validate(row) for row in payload.get("data", [])]
        return items, payload.get("total", len(items))

    async def get_wanted_movies(self, start: int = 0, length: int = -1) -> tuple[list[WantedMovie], int]:
        resp = await self._client.get(
            "/api/movies/wanted", params={"start": start, "length": length}
        )
        resp.raise_for_status()
        payload = resp.json()
        items = [WantedMovie.model_validate(row) for row in payload.get("data", [])]
        return items, payload.get("total", len(items))

    async def iter_all_wanted_episodes(self, page_size: int = 100):
        start = 0
        while True:
            items, total = await self.get_wanted_episodes(start=start, length=page_size)
            for item in items:
                yield item
            start += page_size
            if start >= total or not items:
                break

    async def iter_all_wanted_movies(self, page_size: int = 100):
        start = 0
        while True:
            items, total = await self.get_wanted_movies(start=start, length=page_size)
            for item in items:
                yield item
            start += page_size
            if start >= total or not items:
                break

    # ---- Languages ----

    async def get_languages(self) -> list[dict]:
        """Bazarr's own known-language list (GET /api/system/languages) —
        includes non-standard codes it defines itself (e.g. "pb" for
        Brazilian Portuguese) that aren't in ISO 639-1, so this is the
        authoritative source for what a given code means rather than
        guessing/hardcoding it locally."""
        resp = await self._client.get("/api/system/languages")
        resp.raise_for_status()
        return resp.json()

    # ---- Item detail (existing + missing subtitle languages) ----

    async def get_episode_detail(self, sonarr_episode_id: int) -> EpisodeDetail | None:
        resp = await self._client.get(
            "/api/episodes", params={"episodeid[]": sonarr_episode_id}
        )
        resp.raise_for_status()
        rows = resp.json().get("data", [])
        if not rows:
            return None
        return EpisodeDetail.model_validate(rows[0])

    async def get_movie_detail(self, radarr_id: int) -> MovieDetail | None:
        resp = await self._client.get("/api/movies", params={"radarrid[]": radarr_id})
        resp.raise_for_status()
        rows = resp.json().get("data", [])
        if not rows:
            return None
        return MovieDetail.model_validate(rows[0])

    async def get_all_series(self) -> list[SeriesInfo]:
        """Every series Sonarr/Bazarr knows about — /api/episodes has no
        "give me everything" mode (see get_all_episodes), so this is the
        starting point for listing every episode, and the only source for
        each series' own title (episode rows themselves don't carry it)."""
        resp = await self._client.get("/api/series")
        resp.raise_for_status()
        return [SeriesInfo.model_validate(row) for row in resp.json().get("data", [])]

    async def get_all_episodes(self, series_ids: list[int]) -> list[EpisodeDetail]:
        """Every episode across the given series ids — the FULL episode
        library when passed every id from get_all_series(), not just
        episodes currently missing a subtitle (that's
        iter_all_wanted_episodes instead). Used by the compare tool's
        full-library search, which needs to offer ANY item as a
        translation source regardless of whether Subtitlarr's own poller
        ever considered it "wanted."

        Unlike /api/movies, Bazarr's /api/episodes 404s with no filter at
        all (confirmed live) — it requires seriesid[]/episodeid[], there
        is no "give me everything" mode. Fetched in batches of seriesid[]
        (confirmed live: Bazarr accepts multiple repeated seriesid[]
        params in one request) rather than one request per series, which
        would be hundreds of round trips for a real library."""
        episodes: list[EpisodeDetail] = []
        batch_size = 25
        for start in range(0, len(series_ids), batch_size):
            batch_ids = series_ids[start : start + batch_size]
            resp = await self._client.get("/api/episodes", params={"seriesid[]": batch_ids})
            resp.raise_for_status()
            episodes.extend(EpisodeDetail.model_validate(row) for row in resp.json().get("data", []))
        return episodes

    async def get_all_movies(self) -> list[MovieDetail]:
        """The FULL movie library — see get_all_episodes for why this
        differs from iter_all_wanted_movies."""
        resp = await self._client.get("/api/movies")
        resp.raise_for_status()
        return [MovieDetail.model_validate(row) for row in resp.json().get("data", [])]

    # ---- Subtitle content (read) ----

    async def get_subtitle_contents(self, subtitle_path: str) -> list[SubtitleCue]:
        """Reads an existing subtitle's parsed cues via Bazarr's own filesystem
        access — Subtitlarr never touches the media volume directly."""
        resp = await self._client.get(
            "/api/subtitles/contents", params={"subtitlePath": subtitle_path}
        )
        resp.raise_for_status()
        try:
            body = resp.json()
        except ValueError as exc:
            # A 200 OK with a non-JSON (often empty) body — seen live
            # against a real Bazarr instance for at least one source file,
            # cause unconfirmed (empty/corrupt file on disk is the leading
            # theory). Surfaced as a clear, catchable error instead of a
            # bare JSONDecodeError so it reads as "bad source file" rather
            # than an unexpected crash.
            raise BazarrError(
                f"Bazarr returned a non-JSON response for {subtitle_path!r} "
                f"(HTTP {resp.status_code}, body: {resp.text[:200]!r})"
            ) from exc
        return [SubtitleCue.model_validate(row) for row in body.get("data", [])]

    # ---- Upload (write) ----

    async def upload_episode_subtitle(
        self,
        series_id: int,
        episode_id: int,
        language_code2: str,
        srt_bytes: bytes,
        filename: str = "subtitle.srt",
        forced: bool = False,
        hi: bool = False,
    ) -> None:
        data = {
            "seriesid": str(series_id),
            "episodeid": str(episode_id),
            "language": language_code2,
            "forced": "true" if forced else "false",
            "hi": "true" if hi else "false",
        }
        files = {"file": (filename, srt_bytes, "text/plain")}
        resp = await self._client.post("/api/episodes/subtitles", data=data, files=files)
        if resp.status_code != 204:
            raise BazarrError(
                f"Episode subtitle upload failed ({resp.status_code}): {resp.text}"
            )

    async def upload_movie_subtitle(
        self,
        radarr_id: int,
        language_code2: str,
        srt_bytes: bytes,
        filename: str = "subtitle.srt",
        forced: bool = False,
        hi: bool = False,
    ) -> None:
        data = {
            "radarrid": str(radarr_id),
            "language": language_code2,
            "forced": "true" if forced else "false",
            "hi": "true" if hi else "false",
        }
        files = {"file": (filename, srt_bytes, "text/plain")}
        resp = await self._client.post("/api/movies/subtitles", data=data, files=files)
        if resp.status_code != 204:
            raise BazarrError(
                f"Movie subtitle upload failed ({resp.status_code}): {resp.text}"
            )
