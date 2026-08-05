import sqlite3
from dataclasses import dataclass

from app.bazarr.client import BazarrClient
from app.db import repository


@dataclass
class SourceCandidate:
    path: str
    hi: bool


async def build_source_map(
    client: BazarrClient, item_type: str, bazarr_id: int
) -> dict[str, SourceCandidate]:
    """Returns {lang_code2: SourceCandidate} of subtitles Bazarr already has
    for this item — the candidate pool for translation sources. Forced
    subtitles (usually only cover foreign-language snippets, not full
    dialogue) are always excluded. HI (hearing-impaired) subtitles are kept
    but flagged, so they can be used as a last resort rather than dropped
    outright — see pick_source_language."""
    if item_type == "episode":
        detail = await client.get_episode_detail(bazarr_id)
    else:
        detail = await client.get_movie_detail(bazarr_id)
    if detail is None:
        return {}
    candidates: dict[str, SourceCandidate] = {}
    for sub in detail.subtitles:
        if not sub.path or sub.forced:
            continue
        # Prefer a non-HI track over an HI one for the same language if
        # both exist; only keep the HI one if it's all we have for that code.
        existing = candidates.get(sub.code2)
        if existing is None or (existing.hi and not sub.hi):
            candidates[sub.code2] = SourceCandidate(path=sub.path, hi=sub.hi)
    return candidates


def pick_source_language(
    source_map: dict[str, SourceCandidate], target_lang: str, source_priority: list[str]
) -> str | None:
    """Picks which existing language to translate from. The priority list is
    a PREFERENCE order, not a whitelist — an item is never skipped just
    because its only existing language wasn't explicitly configured (that
    would silently break for anyone whose library isn't English-first).

    Preference order:
      1. Priority-list language, non-HI track
      2. Priority-list language, HI track (better to translate a real
         dialogue track with bracketed sound cues than guess at a
         different language's subtitle, which may not even match)
      3. Any other available language, non-HI
      4. Any other available language, HI
    """
    candidates = [(lang, c) for lang, c in source_map.items() if lang != target_lang]

    for lang in source_priority:
        match = source_map.get(lang)
        if match and lang != target_lang and not match.hi:
            return lang
    for lang in source_priority:
        match = source_map.get(lang)
        if match and lang != target_lang and match.hi:
            return lang
    for lang, c in candidates:
        if not c.hi:
            return lang
    for lang, c in candidates:
        if c.hi:
            return lang
    return None


async def resolve_and_gate(
    conn: sqlite3.Connection,
    client: BazarrClient,
    items: list[sqlite3.Row],
    source_priority: list[str],
) -> list[dict]:
    """For each candidate item, resolves its source language/path via Bazarr.
    Items with no usable source (no existing subtitle in any language other
    than the target itself) are marked skipped_no_source and excluded.
    Returns a list of dicts ready for the translator: item row + resolved
    source_lang/source_path."""
    ready = []
    for item in items:
        source_map = await build_source_map(client, item["item_type"], item["bazarr_id"])
        matched_lang = pick_source_language(source_map, item["target_language"], source_priority)
        if matched_lang is None:
            repository.mark_skipped_no_source(conn, item["id"])
            continue
        ready.append(
            {
                "item": item,
                "source_lang": matched_lang,
                "source_path": source_map[matched_lang].path,
            }
        )
    return ready


def get_age_gated_queue(conn: sqlite3.Connection, age_threshold_days: int) -> list[sqlite3.Row]:
    return repository.get_age_gated_queue(conn, age_threshold_days)


def get_full_translatable_queue(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return repository.get_full_translatable_queue(conn)


def get_filtered_translatable_queue(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    item_type: str | None = None,
    search: str | None = None,
) -> list[sqlite3.Row]:
    return repository.get_translatable_queue_filtered(
        conn, status=status, item_type=item_type, search=search
    )
