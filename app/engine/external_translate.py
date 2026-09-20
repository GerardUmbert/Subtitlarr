"""Runs a standalone translate job submitted via POST /api/external-translate
— content handed directly by a third-party caller (Bazarr or otherwise),
with no items.id/Bazarr identifier behind it. Reuses the same chunking/
cascade-translation/reassembly/disclaimer pipeline a normal Bazarr-sourced
run uses (see engine.translator.translate_item), minus everything specific
to Bazarr: no source fetch, no upload, no items table row."""
import logging

from app import state
from app.db import engine_instances_repo, repository
from app.engine.translator import _batch_token_budget, _translate_batches
from app.providers import registry
from app.providers.languages import language_name
from app.subtitles import srt_io
from app.subtitles.reconciler import verify_full_file_integrity

logger = logging.getLogger(__name__)


class NoEngineConfiguredError(Exception):
    pass


async def run_external_translate_job(conn, job_id: int, source_subs: list, source_lang: str, target_lang: str) -> None:
    """Background task body — mirrors translate_item's own shape (chunk,
    translate, verify, disclaim, compose) but writes its outcome to
    external_translate_jobs instead of items/item_run_log, and never
    touches Bazarr. Any exception is caught and recorded as a failed job
    rather than propagating, since nothing awaits this task directly (see
    state.spawn_background_task)."""
    with state.db_lock:
        repository.mark_external_translate_job_running(conn, job_id)

    try:
        with state.db_lock:
            cascade_instances = engine_instances_repo.get_cascade(conn)
        if not cascade_instances:
            raise NoEngineConfiguredError(
                "No enabled, non-rate-limited engine instance is configured — "
                "add or re-enable one on the Engines page."
            )
        cascade, _name_to_instance_id = registry.build_cascade_providers(cascade_instances)
        active_config = cascade_instances[0]["config"]
        batch_token_budget_override, concurrent_batch_window = registry.batch_settings_for(active_config)
        num_ctx = active_config.get("num_ctx", 8192)

        resolved_batch_budget = _batch_token_budget(num_ctx, batch_token_budget_override)
        batches = srt_io.chunk_cues(source_subs, max_tokens_per_batch=resolved_batch_budget)

        with state.db_lock:
            catalan_vegeta_insults = repository.get_config(conn, "catalan_vegeta_insults", default=False)
        with state.db_lock:
            language_variants = repository.get_config(conn, "language_variants", default={})

        translated_subs, engine_used, model_used = await _translate_batches(
            batches, source_lang, target_lang, cascade, job_id,
            catalan_vegeta_insults, language_variants,
            concurrent_batch_window=concurrent_batch_window,
        )

        verify_full_file_integrity(source_subs, translated_subs)

        disclaimer = srt_io.disclaimer_text(
            target_lang, language_name(source_lang), language_name(target_lang),
            model_name=model_used,
        )
        translated_subs = srt_io.with_ai_disclaimer(translated_subs, disclaimer)
        srt_bytes = srt_io.compose_srt(translated_subs)

        with state.db_lock:
            repository.finish_external_translate_job(
                conn, job_id, status="done",
                result_srt=srt_bytes.decode("utf-8"),
                engine_used=engine_used, model_used=model_used,
            )
    except Exception as exc:
        logger.exception("External translate job %s failed", job_id)
        with state.db_lock:
            repository.finish_external_translate_job(conn, job_id, status="failed", error=str(exc))
