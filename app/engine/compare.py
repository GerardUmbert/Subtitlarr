import asyncio
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.bazarr.client import BazarrClient
from app.db import engine_instances_repo, repository
from app.engine import prefetch
from app.engine.translator import NVIDIA_CONCURRENT_BATCH_WINDOW, _batch_token_budget, _translate_batches
from app.providers import registry
from app.subtitles import srt_io
from app.subtitles.reconciler import TranslationAlignmentError, TranslationIntegrityError, verify_full_file_integrity

logger = logging.getLogger(__name__)

# In-memory cache of Bazarr's FULL episode/movie library (title + existing
# subtitle tracks) — distinct from Subtitlarr's own `items` table, which
# only ever holds what the poller found WANTED (missing a subtitle) at
# some past poll. The compare tool needs to offer ANY item Bazarr knows
# about as a translation source, with ANY target language, not just items
# Subtitlarr's regular pipeline is currently tracking. Refetching the
# whole library on every keystroke would hammer Bazarr for no reason, so
# this is fetched once and reused; call refresh_library_cache() to force
# a refetch (e.g. after adding new media) rather than restarting the app.
_library_cache: list[dict] = []
_library_cache_at: float = 0.0


async def refresh_library_cache(client: BazarrClient) -> int:
    """Fetches Bazarr's full episode + movie library and replaces the
    cache. Each entry is a plain dict — NOT a DB row, NOT tied to any
    items table entry — carrying just enough to search/display and to
    later re-fetch the actual subtitle content on demand (source
    resolution happens lazily in search_library, not eagerly here, so
    this cache stays cheap even for a large library)."""
    global _library_cache, _library_cache_at
    series_list = await client.get_all_series()
    series_titles = {s.sonarrSeriesId: s.title for s in series_list}
    episodes = await client.get_all_episodes([s.sonarrSeriesId for s in series_list])
    movies = await client.get_all_movies()
    entries: list[dict] = []
    for ep in episodes:
        entries.append({
            "item_type": "episode",
            "bazarr_id": ep.sonarrEpisodeId,
            "series_id": ep.sonarrSeriesId,
            "title": ep.title,
            "series_title": series_titles.get(ep.sonarrSeriesId),
            "season_episode": f"S{ep.season:02d}E{ep.episode:02d}",
            "subtitle_langs": sorted({s.code2 for s in ep.subtitles if s.path and not s.forced}),
        })
    for mv in movies:
        entries.append({
            "item_type": "movie",
            "bazarr_id": mv.radarrId,
            "series_id": None,
            "title": mv.title,
            "series_title": None,
            "season_episode": None,
            "subtitle_langs": sorted({s.code2 for s in mv.subtitles if s.path and not s.forced}),
        })
    _library_cache = entries
    _library_cache_at = time.time()
    return len(entries)


def is_library_cached() -> bool:
    return bool(_library_cache)


def search_library(query: str, source_language: str | None, limit: int = 20) -> list[dict]:
    """Searches the cached full library by title (case-insensitive
    substring), optionally restricted to entries that have an existing
    subtitle in `source_language` — mirrors what resolve_and_gate would
    later accept as a usable source, without needing a live Bazarr call
    per keystroke."""
    query_lower = query.lower().strip()
    results = []
    for entry in _library_cache:
        display = f"{entry['series_title']} - {entry['season_episode']} - {entry['title']}" if entry["series_title"] else entry["title"]
        if query_lower and query_lower not in display.lower():
            continue
        if source_language and source_language not in entry["subtitle_langs"]:
            continue
        results.append({**entry, "display_title": display})
        if len(results) >= limit:
            break
    return results

# Nested under the SAME scratch root prefetch.py already uses — that root
# is tempfile.gettempdir(), which is already OS/container-ephemeral (wiped
# on reboot without any extra cleanup code), matching "deleted upon
# reboots" for free. A dedicated "compare" subfolder just keeps compare
# output physically separate from the real prefetch cache, so nothing here
# can ever collide with or be mistaken for a real queued item's cached
# source/translated file.
COMPARE_SCRATCH_ROOT = prefetch.DEFAULT_SCRATCH_ROOT / "compare"


class CompareError(Exception):
    """Raised for compare-tool-specific failures (no source available, bad
    engine instance id, etc.) — distinct from a per-engine translation
    failure, which is captured PER SIDE in CompareResult.error instead of
    raising, so one engine failing doesn't prevent showing the other's
    result."""


@dataclass
class EngineRunResult:
    instance_id: int
    instance_name: str
    model: str
    ok: bool
    error: str | None = None
    srt_path: Path | None = None
    subtitle_text: str | None = None  # composed SRT, for inline diff rendering
    temperature: float | None = None  # the actual value used for this run (override or instance default)
    total_seconds: float = 0.0
    batch_count: int = 0
    cue_count: int = 0
    avg_seconds_per_cue: float = 0.0


@dataclass
class CompareRunResult:
    run_id: str
    item_id: int | None  # Bazarr's own bazarr_id (sonarrEpisodeId/radarrId), NOT a Subtitlarr items.id — None when the source was an uploaded file
    source_lang: str
    target_lang: str
    parallel: bool
    source_text: str | None = None  # composed SRT of the ORIGINAL untranslated source — lets the UI show "what did this line originally say" per row
    results: list[EngineRunResult] = field(default_factory=list)


async def _run_one_engine(
    *,
    instance: dict,
    original_subs: list,
    source_lang: str,
    target_lang: str,
    item_id: int,
    catalan_vegeta_insults: bool,
    language_variants: dict[str, str],
    temperature_override: float | None = None,
) -> EngineRunResult:
    """Translates the SAME already-fetched source cues with one engine
    instance, using the exact same batching/retry/alignment-check pipeline
    a real queue run uses (translator._translate_batches,
    verify_full_file_integrity) — so a comparison reflects genuine
    reliability, not just a lucky/unlucky single call. Deliberately does
    NOT go through translator.translate_item itself: that function writes
    items/item_run_log rows and uploads to Bazarr, neither of which must
    ever happen for a comparison run. No fallback cascade — comparing IS
    the point, so a failure here is reported as this engine's result, not
    silently retried against a different engine.

    temperature_override, when given, replaces the instance's own SAVED
    temperature for just this comparison run — lets the compare tool show
    what temperature actually changes without having to edit and re-save
    the real instance config first."""
    config = instance["config"]
    if temperature_override is not None:
        config = {**config, "temperature": temperature_override}
    provider = registry.build_provider(
        instance["provider_type"], config, instance_name=instance["name"]
    )
    batch_budget_override, concurrent_window = registry.batch_settings_for(config)
    num_ctx = config.get("num_ctx", 8192)
    resolved_budget = _batch_token_budget(num_ctx, batch_budget_override)
    resolved_temperature = config.get("temperature", registry.DEFAULT_TEMPERATURE)

    started = time.monotonic()
    try:
        batches = srt_io.chunk_cues(original_subs, max_tokens_per_batch=resolved_budget)
        translated_subs, _engine_used, model_used = await _translate_batches(
            batches, source_lang, target_lang, [provider], item_id,
            catalan_vegeta_insults, language_variants,
            concurrent_batch_window=concurrent_window or NVIDIA_CONCURRENT_BATCH_WINDOW,
        )
        verify_full_file_integrity(original_subs, translated_subs)
        elapsed = time.monotonic() - started
        srt_bytes = srt_io.compose_srt(translated_subs)
        return EngineRunResult(
            instance_id=instance["id"],
            instance_name=instance["name"],
            model=model_used or provider.model,
            ok=True,
            subtitle_text=srt_bytes.decode("utf-8"),
            temperature=resolved_temperature,
            total_seconds=elapsed,
            batch_count=len(batches),
            cue_count=len(original_subs),
            avg_seconds_per_cue=elapsed / len(original_subs) if original_subs else 0.0,
        )
    except (TranslationAlignmentError, TranslationIntegrityError) as exc:
        elapsed = time.monotonic() - started
        return EngineRunResult(
            instance_id=instance["id"], instance_name=instance["name"], model=provider.model,
            ok=False, error=str(exc), temperature=resolved_temperature, total_seconds=elapsed,
        )
    except Exception as exc:  # noqa: BLE001 - one engine's failure must not abort the other's comparison
        elapsed = time.monotonic() - started
        logger.exception("Compare run: engine instance %d failed", instance["id"])
        return EngineRunResult(
            instance_id=instance["id"], instance_name=instance["name"], model=provider.model,
            ok=False, error=str(exc) or type(exc).__name__, temperature=resolved_temperature,
            total_seconds=elapsed,
        )
    finally:
        await provider.aclose()


def parse_uploaded_srt(raw: bytes, *, label: str) -> list:
    """Parses a user-uploaded .srt file's raw bytes into cues, for either
    the compare tool's "upload a source" mode or its "compare against an
    existing translation" mode. `label` only flavors the error message
    (e.g. "source" vs "reference translation") so a parse failure says
    which of the two uploads was bad."""
    try:
        subs = srt_io.parse_srt_bytes(raw)
    except Exception as exc:  # noqa: BLE001 - surface as a clean 400, not a 500
        raise CompareError(f"Could not parse uploaded {label} file as SRT: {exc}") from exc
    if not subs:
        raise CompareError(f"Uploaded {label} file has no cues")
    return subs


async def run_compare(
    conn: sqlite3.Connection,
    client: BazarrClient,
    *,
    library_item_type: str | None = None,
    library_bazarr_id: int | None = None,
    library_source_lang: str | None = None,
    library_target_lang: str | None = None,
    instance_id_a: int,
    instance_id_b: int | None,
    parallel: bool,
    catalan_vegeta_insults_a: bool | None = None,
    catalan_vegeta_insults_b: bool | None = None,
    temperature_a: float | None = None,
    temperature_b: float | None = None,
    uploaded_source: list | None = None,
    uploaded_source_lang: str | None = None,
    uploaded_target_lang: str | None = None,
) -> CompareRunResult:
    """Fetches ONE source subtitle and translates it with one or two engine
    instances — sequentially or concurrently per `parallel` when both are
    given. Never writes to items/item_run_log, never uploads to Bazarr;
    output SRTs are written only under COMPARE_SCRATCH_ROOT, keyed by a
    fresh run_id, so they can never collide with or overwrite a real
    queued/already-translated item's file.

    Source is EITHER an item picked from search_library() (library_*
    params set — ANY item Bazarr's full episode/movie list knows about,
    with an EXPLICITLY chosen source AND target language, independent of
    whatever Subtitlarr's own items table considers "wanted") OR a raw
    uploaded file (uploaded_source/uploaded_source_lang/
    uploaded_target_lang set instead).

    instance_id_b is optional — None runs ONLY instance_id_a (used by the
    compare tool's "against an uploaded reference translation" mode, where
    the second "side" is a static file, not a second engine call; running
    a second engine there would be pure waste).

    catalan_vegeta_insults_a/_b are per-SIDE, independent of each other and
    of the saved Language Rules setting (None on either falls back to that
    saved setting) — the whole point of the compare tool is letting you
    see what a toggle actually changes, so it must be settable differently
    per engine rather than forced identical on both sides.

    temperature_a/_b work the same way — None on either side falls back to
    that instance's own SAVED temperature (registry.DEFAULT_TEMPERATURE if
    that instance predates the setting existing)."""
    instance_a = engine_instances_repo.get_instance(conn, instance_id_a)
    if instance_a is None:
        raise CompareError("Selected engine instance no longer exists")
    instance_b = None
    if instance_id_b is not None:
        instance_b = engine_instances_repo.get_instance(conn, instance_id_b)
        if instance_b is None:
            raise CompareError("Selected engine instance no longer exists")

    if library_bazarr_id is not None:
        if not library_item_type or not library_source_lang or not library_target_lang:
            raise CompareError("Library source requires item_type, source_language, and target_language")
        if library_item_type == "episode":
            detail = await client.get_episode_detail(library_bazarr_id)
        else:
            detail = await client.get_movie_detail(library_bazarr_id)
        if detail is None:
            raise CompareError("Selected item no longer exists in Bazarr")
        match = next(
            (s for s in detail.subtitles if s.code2 == library_source_lang and s.path and not s.forced),
            None,
        )
        if match is None:
            raise CompareError(
                f"No existing {library_source_lang!r} subtitle found for this item anymore — it may have been removed."
            )
        cues = await client.get_subtitle_contents(match.path)
        original_subs = srt_io.cues_from_bazarr(cues)
        if not original_subs:
            raise CompareError("Source subtitle has no cues")
        source_lang = library_source_lang
        target_lang = library_target_lang
        result_item_id = library_bazarr_id
    else:
        if not uploaded_source or not uploaded_source_lang or not uploaded_target_lang:
            raise CompareError("Uploaded source requires source_lang and target_lang")
        original_subs = uploaded_source
        source_lang = uploaded_source_lang
        target_lang = uploaded_target_lang
        result_item_id = None

    run_id = uuid.uuid4().hex[:12]
    saved_catalan_vegeta_insults = repository.get_config(conn, "catalan_vegeta_insults", default=False)
    resolved_insults_a = catalan_vegeta_insults_a if catalan_vegeta_insults_a is not None else saved_catalan_vegeta_insults
    resolved_insults_b = catalan_vegeta_insults_b if catalan_vegeta_insults_b is not None else saved_catalan_vegeta_insults
    language_variants = repository.get_config(conn, "language_variants", default={})

    common_kwargs = dict(
        original_subs=original_subs, source_lang=source_lang, target_lang=target_lang,
        item_id=result_item_id if result_item_id is not None else -1,
        language_variants=language_variants,
    )
    if instance_b is None:
        results = [
            await _run_one_engine(
                instance=instance_a, catalan_vegeta_insults=resolved_insults_a,
                temperature_override=temperature_a, **common_kwargs,
            )
        ]
    elif parallel:
        results = list(await asyncio.gather(
            _run_one_engine(
                instance=instance_a, catalan_vegeta_insults=resolved_insults_a,
                temperature_override=temperature_a, **common_kwargs,
            ),
            _run_one_engine(
                instance=instance_b, catalan_vegeta_insults=resolved_insults_b,
                temperature_override=temperature_b, **common_kwargs,
            ),
        ))
    else:
        results = [
            await _run_one_engine(
                instance=instance_a, catalan_vegeta_insults=resolved_insults_a,
                temperature_override=temperature_a, **common_kwargs,
            ),
            await _run_one_engine(
                instance=instance_b, catalan_vegeta_insults=resolved_insults_b,
                temperature_override=temperature_b, **common_kwargs,
            ),
        ]

    run_dir = COMPARE_SCRATCH_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    for label, result in zip("ab", results):
        if result.ok and result.subtitle_text is not None:
            path = run_dir / f"{label}.srt"
            path.write_text(result.subtitle_text, encoding="utf-8")
            result.srt_path = path

    return CompareRunResult(
        run_id=run_id, item_id=result_item_id, source_lang=source_lang, target_lang=target_lang,
        parallel=parallel, source_text=srt_io.compose_srt(original_subs).decode("utf-8"),
        results=results,
    )
