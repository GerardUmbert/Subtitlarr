from pydantic import BaseModel


class LanguageInfo(BaseModel):
    name: str
    code2: str
    code3: str
    forced: bool = False
    hi: bool = False


class SubtitleInfo(LanguageInfo):
    path: str | None = None
    file_size: int | None = None
    embedded_track_id: int | None = None


class AudioLanguageInfo(BaseModel):
    name: str
    code2: str
    code3: str


class WantedEpisode(BaseModel):
    seriesTitle: str
    episode_number: str
    episodeTitle: str
    missing_subtitles: list[LanguageInfo] = []
    sonarrSeriesId: int
    sonarrEpisodeId: int
    sceneName: str | None = None
    tags: list[str] = []
    seriesType: str | None = None


class WantedMovie(BaseModel):
    title: str
    missing_subtitles: list[LanguageInfo] = []
    radarrId: int
    sceneName: str | None = None
    tags: list[str] = []


class WantedPage(BaseModel):
    data: list
    total: int


class EpisodeDetail(BaseModel):
    audio_language: AudioLanguageInfo | list[AudioLanguageInfo] | None = None
    episode: int
    missing_subtitles: list[LanguageInfo] = []
    monitored: bool
    path: str
    season: int
    sonarrEpisodeId: int
    sonarrSeriesId: int
    subtitles: list[SubtitleInfo] = []
    title: str
    sceneName: str | None = None


class MovieDetail(BaseModel):
    audio_language: AudioLanguageInfo | list[AudioLanguageInfo] | None = None
    missing_subtitles: list[LanguageInfo] = []
    monitored: bool
    path: str
    radarrId: int
    subtitles: list[SubtitleInfo] = []
    title: str
    sceneName: str | None = None


class SubtitleCueTime(BaseModel):
    hours: int
    minutes: int
    seconds: int
    total_seconds: int
    microseconds: int


class SubtitleCue(BaseModel):
    index: int
    content: str
    proprietary: str = ""
    start: SubtitleCueTime
    end: SubtitleCueTime
