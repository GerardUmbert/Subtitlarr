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
    """Refreshes items from Bazarr's wanted lists. Bazarr's full wanted set
    is fetched FIRST, then repository.purge_unsynced_items deletes only the
    not-yet-translated items that set no longer contains, before upserting —
    so wanted/translatable/no_source stats stay accurate without wiping and
    reinserting everything Bazarr still wants every single poll (that used
    to reset first_seen_wanted to "now" on every still-wanted pending item,
    permanently starving the age-gated scheduled run — see
    purge_unsynced_items' docstring). Only 'done'/'translated_pending_upload'
    items are exempt from purging entirely — those are Subtitlarr's own
    durable "translated" record and don't depend on Bazarr still listing the
    item as wanted.
    Upserts newly-seen (item, missing target language) pairs, stamping
    first_seen_wanted only on first sight, and eagerly previews each new
    item's source language for display."""
    episodes_seen = 0
    movies_seen = 0
    with state.db_lock:
        source_priority = repository.get_config(conn, "source_lang_priority", default=[])
        target_allowlist = set(repository.get_config(conn, "target_lang_allowlist", default=[]))

    wanted_episodes = []
    async for wanted in client.iter_all_wanted_episodes():
        wanted_episodes.append(wanted)
        episodes_seen += 1

    wanted_movies = []
    async for wanted in client.iter_all_wanted_movies():
        wanted_movies.append(wanted)
        movies_seen += 1

    def _wanted_keys(wanted_items, item_type, id_field):
        keys = set()
        for wanted in wanted_items:
            for lang in wanted.missing_subtitles:
                if target_allowlist and lang.code2 not in target_allowlist:
                    continue
                keys.add((item_type, getattr(wanted, id_field), lang.code2))
        return keys

    still_wanted = _wanted_keys(wanted_episodes, "episode", "sonarrEpisodeId") | _wanted_keys(
        wanted_movies, "movie", "radarrId"
    )

    with state.db_lock:
        purged = repository.purge_unsynced_items(conn, still_wanted)
    if purged:
        logger.info("Purged %d unsynced item(s) ahead of fresh poll", purged)

    for wanted in wanted_episodes:
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

    for wanted in wanted_movies:
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

    logger.info("Poll complete: %d episodes, %d movies seen as wanted", episodes_seen, movies_seen)
    return {"episodes_seen": episodes_seen, "movies_seen": movies_seen}
