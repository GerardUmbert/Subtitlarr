"""Accepts a translation produced OUTSIDE any configured provider — by a
human, or by an assistant reading source text from
GET /api/queue/{id}/manual-translation/source and posting a translation
back — and runs it through the same reassembly/integrity/disclaimer/
upload/tracking steps translator.translate_item's success path uses for
a real provider's output. No LLM call happens here at all.

engine_used/model_used are recorded as "manual"/whatever model_name the
caller supplies (not a real provider name), so this stays honestly
distinguishable from an actual API-driven translation everywhere that
data surfaces: the Queue's Model column, History, and the disclaimer
line embedded in the subtitle itself.
"""

import logging
import sqlite3

from app import state
from app.bazarr.client import BazarrClient
from app.db import repository
from app.providers.languages import language_name
from app.subtitles import srt_io
from app.subtitles.reconciler import (
    TranslationAlignmentError,
    TranslationIntegrityError,
    reassemble,
    verify_full_file_integrity,
)

logger = logging.getLogger(__name__)

MANUAL_ENGINE_NAME = "manual"


class ManualTranslationError(Exception):
    """Raised for a rejected submission (bad source, failed alignment/
    integrity check) — the caller (API layer) turns this into a 422, same
    as any other per-item validation failure."""


async def submit_manual_translation(
    conn: sqlite3.Connection,
    client: BazarrClient,
    item: sqlite3.Row,
    source_lang: str,
    source_subtitle_path: str,
    translated_text: str,
    model_name: str,
) -> dict:
    """translated_text must be in the same "index\\ncontent" blocks
    format a real provider's response is — see srt_io.extract_dialogue_text
    for the exact shape a caller should match when producing it.
    add_ai_disclaimer is always on here: a manual translation with no
    disclaimer at all would look indistinguishable from a genuine
    pre-existing (non-Subtitlarr) subtitle to the exact guard this
    project relies on elsewhere (translator.py's is_own_prior_upload
    check) — an untagged manual translation would get silently
    re-translated as if it were external the next time this item is
    touched."""
    item_id = item["id"]
    target_lang = item["target_language"]

    cues = await client.get_subtitle_contents(source_subtitle_path)
    original_subs = srt_io.cues_from_bazarr(cues)
    if not original_subs:
        raise ManualTranslationError("Source subtitle has no cues.")

    with state.db_lock:
        repository.update_item_status(conn, item_id, "translating", mark_attempt=True)

    try:
        translated_subs = reassemble(original_subs, translated_text)
    except (TranslationAlignmentError, TranslationIntegrityError) as exc:
        with state.db_lock:
            repository.update_item_status(conn, item_id, "failed", error_message=str(exc))
        with state.db_lock:
            repository.log_item_attempt(
                conn, item_id, None, "failed",
                engine_used=MANUAL_ENGINE_NAME, model_used=model_name, error_message=str(exc),
            )
        raise ManualTranslationError(str(exc)) from exc

    try:
        verify_full_file_integrity(original_subs, translated_subs)
    except TranslationIntegrityError as exc:
        with state.db_lock:
            repository.update_item_status(conn, item_id, "failed", error_message=str(exc))
        with state.db_lock:
            repository.log_item_attempt(
                conn, item_id, None, "failed",
                engine_used=MANUAL_ENGINE_NAME, model_used=model_name, error_message=str(exc),
            )
        raise ManualTranslationError(str(exc)) from exc

    disclaimer = srt_io.disclaimer_text(
        target_lang, language_name(source_lang), language_name(target_lang), model_name=model_name,
    )
    translated_subs = srt_io.with_ai_disclaimer(translated_subs, disclaimer)
    srt_bytes = srt_io.compose_srt(translated_subs)

    if item["item_type"] == "episode":
        await client.upload_episode_subtitle(
            series_id=item["series_id"], episode_id=item["bazarr_id"],
            language_code2=target_lang, srt_bytes=srt_bytes,
        )
    else:
        await client.upload_movie_subtitle(
            radarr_id=item["bazarr_id"], language_code2=target_lang, srt_bytes=srt_bytes,
        )

    with state.db_lock:
        repository.update_item_status(
            conn, item_id, "done",
            source_language=source_lang, engine_used=MANUAL_ENGINE_NAME, model_used=model_name,
            mark_completed=True,
        )
    with state.db_lock:
        repository.set_source_is_external(conn, item_id, False)
    with state.db_lock:
        repository.mark_disclaimer_model_tagged(conn, item_id)
    with state.db_lock:
        repository.log_item_attempt(
            conn, item_id, None, "done", engine_used=MANUAL_ENGINE_NAME, model_used=model_name,
        )

    logger.info("Item %d: manual translation accepted (%s), %d cues", item_id, model_name, len(original_subs))
    return {
        "item_id": item_id,
        "status": "done",
        "engine_used": MANUAL_ENGINE_NAME,
        "model_used": model_name,
        "cue_count": len(original_subs),
    }
