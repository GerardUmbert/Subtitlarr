import logging
import sqlite3

from app import state
from app.bazarr.client import BazarrClient
from app.db import repository
from app.engine import selector

logger = logging.getLogger(__name__)


async def _resolve_and_preview_source(
    conn: sqlite3.Connection, client: BazarrClient, item_type: str, bazarr_id: int,
    target_language: str, source_priority: list[str],
) -> None:
    """Eagerly resolves and stores which source language WOULD be used for
    this (item, target_language) pair, purely so the Queue UI can show a
    real language instead of '?' before the item has ever been translated.
    Costs one extra Bazarr detail call per wanted item per poll — accepted
    tradeoff for always-visible source languages over poll speed/API load.

    Also re-checks skipped_no_source items, not just pending ones — Bazarr's
    library can gain a usable source after an item was first skipped (e.g. a
    new-language subtitle appears later), and without this the item would
    stay invisible to every future run forever, even once a real source
    exists (confirmed live: an EN subtitle became available after the item
    was already marked skipped_no_source, and nothing ever re-checked it)."""
    with state.db_lock:
        item = repository.get_item_by_bazarr_id(conn, item_type, bazarr_id, target_language)
    if item is None or item["status"] not in ("pending", "skipped_no_source"):
        return
    source_map = await selector.build_source_map(client, item_type, bazarr_id)
    matched_lang = selector.pick_source_language(source_map, target_language, source_priority)
    if matched_lang is None:
        with state.db_lock:
            repository.mark_skipped_no_source(conn, item["id"])
    else:
        with state.db_lock:
            repository.set_resolved_source_language(conn, item["id"], matched_lang)


async def poll_once(conn: sqlite3.Connection, client: BazarrClient) -> dict:
    """Refreshes items from Bazarr's wanted lists. Purges every item not yet
    translated (repository.purge_unsynced_items) before re-syncing, so
    wanted/translatable/no_source stats are always rebuilt fresh from
    Bazarr's current wanted list rather than accumulating stale rows across
    polls (e.g. a transient spike from toggling Bazarr's "treat bundled
    subtitles as downloaded" setting would otherwise inflate counts
    permanently). Only 'done'/'translated_pending_upload' items survive the
    purge — those are Subtitlarr's own durable "translated" record and
    don't depend on Bazarr still listing the item as wanted.
    Upserts newly-seen (item, missing target language) pairs, stamping
    first_seen_wanted only on first sight, and eagerly previews each new
    item's source language for display."""
    with state.db_lock:
        purged = repository.purge_unsynced_items(conn)
    if purged:
        logger.info("Purged %d unsynced item(s) ahead of fresh poll", purged)

    episodes_seen = 0
    movies_seen = 0
    with state.db_lock:
        source_priority = repository.get_config(conn, "source_lang_priority", default=[])
        target_allowlist = set(repository.get_config(conn, "target_lang_allowlist", default=[]))

    async for wanted in client.iter_all_wanted_episodes():
        for lang in wanted.missing_subtitles:
            # Empty allowlist = no restriction. A non-empty one lets Bazarr's
            # profile keep wanting a language purely as a fallback TRANSLATION
            # SOURCE (e.g. EN) without Subtitlarr ever creating a job to
            # translate INTO it — Bazarr's wanted-list is otherwise the only
            # thing that decides targets, so this is a deliberate opt-in filter
            # on top of it, not a replacement for it.
            if target_allowlist and lang.code2 not in target_allowlist:
                continue
            with state.db_lock:
                repository.upsert_item_seen(
                    conn,
                    item_type="episode",
                    bazarr_id=wanted.sonarrEpisodeId,
                    series_id=wanted.sonarrSeriesId,
                    title=wanted.episodeTitle,
                    series_title=wanted.seriesTitle,
                    season_episode=wanted.episode_number,
                    target_language=lang.code2,
                )
            await _resolve_and_preview_source(
                conn, client, "episode", wanted.sonarrEpisodeId, lang.code2, source_priority
            )
        episodes_seen += 1

    async for wanted in client.iter_all_wanted_movies():
        for lang in wanted.missing_subtitles:
            if target_allowlist and lang.code2 not in target_allowlist:
                continue
            with state.db_lock:
                repository.upsert_item_seen(
                    conn,
                    item_type="movie",
                    bazarr_id=wanted.radarrId,
                    series_id=None,
                    title=wanted.title,
                    series_title=None,
                    season_episode=None,
                    target_language=lang.code2,
                )
            await _resolve_and_preview_source(
                conn, client, "movie", wanted.radarrId, lang.code2, source_priority
            )
        movies_seen += 1

    logger.info("Poll complete: %d episodes, %d movies seen as wanted", episodes_seen, movies_seen)
    return {"episodes_seen": episodes_seen, "movies_seen": movies_seen}
