import asyncio
import logging
import sqlite3
import time
from pathlib import Path

from app.bazarr.client import BazarrClient
from app.db import repository
from app.engine import run_events, upload_queue
from app.providers.base import (
    ProviderContentBlockedError,
    ProviderError,
    ProviderRateLimitedError,
    TranslationProvider,
)
from app.providers.languages import language_name
from app.subtitles import srt_io
from app.subtitles.reconciler import (
    TranslationAlignmentError,
    TranslationIntegrityError,
    reassemble,
    verify_full_file_integrity,
)

logger = logging.getLogger(__name__)


class NoSourceLanguageError(Exception):
    pass


class RunCancelledError(Exception):
    """Raised inside _translate_batches when cancel_check() reports the
    run was stopped mid-item — unlike the old between-items-only cancel,
    this can interrupt an item partway through its own batches. Caught by
    translate_item and recorded as a distinct 'cancelled' outcome, not
    lumped in with a genuine provider/translation failure."""


async def resolve_source_language(
    existing_subtitle_paths: dict[str, str], source_priority: list[str]
) -> tuple[str, str]:
    """Given {lang_code2: path} of existing subtitles and an ordered
    preference list, returns (lang_code2, path) for the first available
    match. Raises NoSourceLanguageError if none of the priority languages
    are present (caller should mark the item skipped_no_source)."""
    for lang in source_priority:
        if lang in existing_subtitle_paths:
            return lang, existing_subtitle_paths[lang]
    raise NoSourceLanguageError(
        f"None of the preferred source languages {source_priority} are available; "
        f"existing: {list(existing_subtitle_paths.keys())}"
    )


async def _call_provider(
    provider: TranslationProvider,
    dialogue_text: str,
    source_lang: str,
    target_lang: str,
    catalan_vegeta_insults: bool,
    language_variants: dict[str, str] | None,
    item_id: int,
    batch_index: int,
    batch_total: int,
    on_call_result=None,
) -> str:
    """One raw translate() call with the diagnostic timing log lines —
    factored out of _try_cascade so the sending/response log shape stays
    identical to before this was a cascade instead of active/fallback.
    on_call_result(provider, rate_limited: bool), if given, is called
    after EVERY attempt — True on a ProviderRateLimitedError (feeds the
    rate-limit cooldown counter), False on success (resets it) — before
    a rate-limit exception propagates. Only ProviderRateLimitedError
    triggers this; ProviderContentBlockedError/other failures aren't
    evidence the INSTANCE itself is unreachable, just that this specific
    content tripped its filter, so they don't count toward the cooldown."""
    call_started = time.monotonic()
    logger.info(
        "Sending translate() call for item %d batch %d/%d to %s (%d chars)",
        item_id, batch_index, batch_total, provider.name, len(dialogue_text),
    )
    try:
        llm_response = await provider.translate(
            dialogue_text, source_lang, target_lang, catalan_vegeta_insults, language_variants
        )
    except ProviderRateLimitedError:
        if on_call_result is not None:
            on_call_result(provider, True)
        raise
    if on_call_result is not None:
        on_call_result(provider, False)
    logger.info(
        "translate() call for item %d (%s) took %.2fs",
        item_id, provider.name, time.monotonic() - call_started,
    )
    return llm_response


def _fallback_log_line(prior_name: str, item_id: int, exc: Exception, next_name: str) -> str:
    """Exact wording preserved from the original except blocks for the two
    cases app.engine.log_events actually parses into a structured Events-
    tab row ("failed again" / "blocked content (...)") — changing either
    would silently break that regex match. The alignment/integrity case
    was never a distinct parsed EventType even before this rewrite (its
    log line had no matching pattern), so its wording here is free to be
    whatever reads best."""
    if isinstance(exc, ProviderContentBlockedError):
        return (
            f"Provider {prior_name} blocked content for item {item_id} "
            f"({exc}); falling back to {next_name}"
        )
    if isinstance(exc, (TranslationAlignmentError, TranslationIntegrityError)):
        return (
            f"Provider {prior_name} produced an unreliable response for item {item_id} "
            f"({exc}); falling back to {next_name}"
        )
    return f"Provider {prior_name} failed again for item {item_id}; falling back to {next_name}"


async def _try_cascade(
    cascade: list[TranslationProvider],
    start_index: int,
    dialogue_text: str,
    source_lang: str,
    target_lang: str,
    catalan_vegeta_insults: bool,
    language_variants: dict[str, str] | None,
    item_id: int,
    batch_index: int,
    batch_total: int,
    retry_pause_seconds: float,
    run_id: int | None,
    triggering_exc: Exception,
    on_call_result=None,
) -> tuple[str, int]:
    """Tries cascade[start_index:], in order, returning
    (llm_response, engine_index) on first success. `triggering_exc` is
    whatever exception caused THIS hop to be attempted — its type/message
    drives the log line's exact wording (see _fallback_log_line) so
    Events-tab parsing keeps working. Raises the LAST error if every
    remaining instance fails. Called with start_index == the index that
    JUST failed + 1, so it never retries the instance that produced the
    failure being handled."""
    exc: Exception = triggering_exc
    for index in range(start_index, len(cascade)):
        provider = cascade[index]
        prior = cascade[index - 1]
        line = _fallback_log_line(prior.name, item_id, exc, provider.name)
        logger.warning(line)
        if run_id is not None:
            run_events.emit(run_id, item_id, batch_index, batch_total, "fell_back", line)
        try:
            llm_response = await _call_provider(
                provider, dialogue_text, source_lang, target_lang,
                catalan_vegeta_insults, language_variants, item_id, batch_index, batch_total,
                on_call_result,
            )
            return llm_response, index
        except (ProviderRateLimitedError, ProviderContentBlockedError) as new_exc:
            exc = new_exc
            continue
    raise exc


async def _translate_batch(
    batch: list,
    source_lang: str,
    target_lang: str,
    cascade: list[TranslationProvider],
    item_id: int,
    catalan_vegeta_insults: bool = False,
    language_variants: dict[str, str] | None = None,
    retry_pause_seconds: float = 0,
    run_id: int | None = None,
    batch_index: int = 1,
    batch_total: int = 1,
    on_call_result=None,
) -> tuple[list, str, str]:
    """Translates and reconciles ONE batch of cues, walking the cascade on
    failure. Returns (reassembled_subs_for_this_batch, engine_used, model_used).

    cascade[0] is the primary/active instance; cascade[1:] are fallback
    candidates, in order — a run's caller builds this fresh per item from
    the current engine_instances cascade (already filtered to enabled,
    non-rate-limited instances).

    A ProviderRateLimitedError (429, timeout, connection failure, or a
    transient 5xx server error — see nvidia_provider.py) against
    cascade[0] gets ONE retry against that SAME instance first, since
    these are often gone within seconds and don't warrant abandoning it
    immediately. Only if that retry ALSO fails does it walk the rest of
    the cascade.

    A real 429 (retry_after_seconds set) is NOT slept on here — the
    provider itself (see NvidiaProvider._rate_limited_until) already
    blocks every call, across every batch and every item in the run, until
    the shared per-minute cooldown clears, so sleeping again here would
    just double the wait. Everything else (timeouts, transient 5xx, no
    shared gate to rely on) sleeps for pause_between_items_seconds before
    its one retry."""
    dialogue_text = srt_io.extract_dialogue_text(batch)
    active_provider = cascade[0]
    engine_used = active_provider.name
    model_used = active_provider.model
    engine_index = 0

    try:
        llm_response = await _call_provider(
            active_provider, dialogue_text, source_lang, target_lang,
            catalan_vegeta_insults, language_variants, item_id, batch_index, batch_total,
            on_call_result,
        )
    except ProviderRateLimitedError as exc:
        # A real rate limit (retry_after_seconds set) relies entirely on
        # the provider's own shared gate — sleeping here too would wait
        # twice. Anything else sleeps for pause_between_items_seconds.
        wait_seconds = 0 if exc.retry_after_seconds is not None else retry_pause_seconds
        actual_wait = exc.retry_after_seconds if exc.retry_after_seconds is not None else wait_seconds
        logger.warning(
            "Provider %s rate-limited/unreachable for item %d (%s); retrying once after %.0fs",
            active_provider.name, item_id, exc, actual_wait,
        )
        if run_id is not None:
            run_events.emit(
                run_id, item_id, batch_index, batch_total, "retrying",
                f"{active_provider.name}: {exc} — retrying in {actual_wait:.0f}s",
            )
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
        try:
            llm_response = await _call_provider(
                active_provider, dialogue_text, source_lang, target_lang,
                catalan_vegeta_insults, language_variants, item_id, batch_index, batch_total,
                on_call_result,
            )
            if run_id is not None:
                run_events.emit(
                    run_id, item_id, batch_index, batch_total, "retry_succeeded",
                    f"{active_provider.name}: succeeded on retry",
                )
        except ProviderRateLimitedError as retry_exc:
            if len(cascade) < 2:
                raise
            llm_response, engine_index = await _try_cascade(
                cascade, 1, dialogue_text, source_lang, target_lang,
                catalan_vegeta_insults, language_variants, item_id, batch_index, batch_total,
                retry_pause_seconds, run_id, retry_exc, on_call_result,
            )
            engine_used = cascade[engine_index].name
            model_used = cascade[engine_index].model
    except ProviderContentBlockedError as blocked_exc:
        # No same-instance retry here — unlike ProviderRateLimitedError,
        # retrying the SAME instance on a content-policy block would just
        # trip the same filter again (the content didn't change). Go
        # straight to the next cascade entry, if any, since a different
        # provider/model may not flag the same content at all. Confirmed
        # live: a real batch failed outright on Gemini (PROHIBITED_CONTENT)
        # with a fallback engine configured but never even attempted —
        # this is the gap that fixes.
        if len(cascade) < 2:
            raise
        llm_response, engine_index = await _try_cascade(
            cascade, 1, dialogue_text, source_lang, target_lang,
            catalan_vegeta_insults, language_variants, item_id, batch_index, batch_total,
            retry_pause_seconds, run_id, blocked_exc, on_call_result,
        )
        engine_used = cascade[engine_index].name
        model_used = cascade[engine_index].model

    try:
        return reassemble(batch, llm_response), engine_used, model_used
    except (TranslationAlignmentError, TranslationIntegrityError) as exc:
        # A repetition loop or otherwise-unreliable response is a property
        # of THIS provider's output, not a request-level failure the
        # ProviderRateLimitedError/ProviderContentBlockedError handlers
        # above would ever see — translate() returned 200 with real text,
        # it just wasn't trustworthy. Confirmed live: several items failed
        # outright on Gemini repetition loops with a fallback engine
        # configured but never attempted, same class of gap as the
        # content-block case above. Only retry once, walking the cascade
        # from the NEXT entry after whichever one just produced this bad
        # output — never loop back to an instance that already produced an
        # unreliable response, since a repetition loop is generally
        # reproducible on retry.
        if engine_index + 1 >= len(cascade):
            raise
        llm_response, engine_index = await _try_cascade(
            cascade, engine_index + 1, dialogue_text, source_lang, target_lang,
            catalan_vegeta_insults, language_variants, item_id, batch_index, batch_total,
            retry_pause_seconds, run_id, exc, on_call_result,
        )
        engine_used = cascade[engine_index].name
        model_used = cascade[engine_index].model
        return reassemble(batch, llm_response), engine_used, model_used


# Cloud-provider-only (NVIDIA, OpenRouter, Groq, Gemini): their endpoints
# have no local VRAM/GPU contention (unlike Ollama, where concurrent
# requests would just serialize against the same model instance anyway —
# no real speedup, and it fights the watchdog/timeout logic that assumes
# one request at a time). Kept well under each provider's own documented
# (or, for Gemini, account-tier-dependent) per-minute request ceiling —
# see RATE_LIMIT_RPM in nvidia_provider.py / openrouter_provider.py /
# groq_provider.py. Confirmed safe: each batch's cues carry their own real
# subtitle index baked into the prompt (srt_io.extract_dialogue_text), and
# reassemble() maps translated content back onto the ORIGINAL cue list by
# matching that index, never by position/arrival order — and
# asyncio.gather() itself returns results in the same order as its input
# regardless of which one resolves first. So concurrent batches can't
# misorder cues even though responses can arrive out of order.
NVIDIA_CONCURRENT_BATCH_WINDOW = 4

# Set of provider names that get the windowed-concurrency treatment above
# instead of translating batches strictly sequentially.
_CONCURRENT_PROVIDERS = {"nvidia", "openrouter", "groq", "gemini"}


async def _translate_batches(
    batches: list[list],
    source_lang: str,
    target_lang: str,
    cascade: list[TranslationProvider],
    item_id: int,
    catalan_vegeta_insults: bool = False,
    language_variants: dict[str, str] | None = None,
    retry_pause_seconds: float = 0,
    run_id: int | None = None,
    concurrent_batch_window: int = NVIDIA_CONCURRENT_BATCH_WINDOW,
    on_call_result=None,
    cancel_check=None,
) -> tuple[list, str, str]:
    """Runs all of an item's batches, sequentially unless the PRIMARY
    (cascade[0]) instance's provider_type is one of the cloud ones in
    _CONCURRENT_PROVIDERS (windowed concurrency there — see
    NVIDIA_CONCURRENT_BATCH_WINDOW; keyed off provider_type, not the
    possibly-customized .name, so a renamed "Gemini (main)" instance
    still gets concurrency). Returns (all translated subs in original
    order, engine_used, model_used — the last batch's engine/model wins if
    a fallback occurred partway).

    cancel_check, if given, is called before EVERY batch (sequential path)
    or before every WINDOW of batches (concurrent path — a window's
    requests are already in flight together via asyncio.gather(), so
    cancellation can't interrupt mid-window, only between them). Raises
    RunCancelledError the moment it returns True, so a Stop click can
    interrupt an item partway through its own batches, not just between
    items."""
    translated_subs: list = []
    active_provider = cascade[0]
    engine_used = active_provider.name
    model_used = active_provider.model
    batch_total = len(batches)

    if active_provider.provider_type not in _CONCURRENT_PROVIDERS:
        for i, batch in enumerate(batches):
            if cancel_check is not None and cancel_check():
                raise RunCancelledError(
                    f"Run cancelled after {i}/{batch_total} batch(es) for item {item_id}"
                )
            batch_result, batch_engine, batch_model = await _translate_batch(
                batch, source_lang, target_lang, cascade, item_id,
                catalan_vegeta_insults, language_variants, retry_pause_seconds, run_id, i + 1, batch_total,
                on_call_result,
            )
            translated_subs.extend(batch_result)
            engine_used = batch_engine
            model_used = batch_model
        return translated_subs, engine_used, model_used

    for window_start in range(0, len(batches), concurrent_batch_window):
        if cancel_check is not None and cancel_check():
            raise RunCancelledError(
                f"Run cancelled after {window_start}/{batch_total} batch(es) for item {item_id}"
            )
        window = batches[window_start : window_start + concurrent_batch_window]
        window_started = time.monotonic()
        results = await asyncio.gather(
            *(
                _translate_batch(
                    batch, source_lang, target_lang, cascade, item_id,
                    catalan_vegeta_insults, language_variants, retry_pause_seconds, run_id,
                    window_start + offset + 1, batch_total, on_call_result,
                )
                for offset, batch in enumerate(window)
            )
        )
        logger.info(
            "Item %d: %s window of %d batch(es) (starting at batch %d) took %.2fs",
            item_id, active_provider.name, len(window), window_start + 1,
            time.monotonic() - window_started,
        )
        for batch_result, batch_engine, batch_model in results:  # gather() preserves input order
            translated_subs.extend(batch_result)
            engine_used = batch_engine
            model_used = batch_model
    return translated_subs, engine_used, model_used


def _batch_token_budget(num_ctx: int, override: int = 0) -> int:
    """Derives a per-batch dialogue token budget from the provider's
    configured context window, so a larger num_ctx actually results in
    fewer, larger batches instead of always using a fixed small chunk size.
    Reserves headroom for: the system prompt (~150 tokens), the model's own
    response (translated text can run longer than the source, especially
    into languages with more verbose grammar — budgeted at ~1.3x), and a
    safety margin, so the total round-trip comfortably fits within num_ctx
    rather than risking truncation right at the edge of the window.

    Fitting within num_ctx is necessary but not sufficient — small models
    lose reliable numbered-output formatting well before they run out of
    raw context (observed live: a 900-token batch reliably recovered ~61/61
    cues, a ~3500-token batch recovered as few as 1/106). `override`, when
    set (>0), bypasses the formula entirely with a fixed value the user has
    tuned for their own model's actual formatting reliability."""
    if override > 0:
        return override
    overhead_tokens = 150
    usable = max(0, num_ctx - overhead_tokens)
    # response budget ~= 1.3x the dialogue budget, so dialogue + response
    # together fit within `usable`: dialogue * (1 + 1.3) <= usable
    dialogue_budget = int(usable / 2.3)
    return max(400, dialogue_budget)  # never go below the old conservative floor


async def translate_item(
    conn: sqlite3.Connection,
    client: BazarrClient,
    item: sqlite3.Row,
    source_lang: str,
    source_subtitle_path: str,
    cascade: list[TranslationProvider],
    run_id: int | None,
    add_ai_disclaimer: bool = True,
    num_ctx: int = 8192,
    batch_token_budget_override: int = 0,
    cached_source_path: Path | None = None,
    queue_uploads: bool = False,
    retry_pause_seconds: float = 0,
    concurrent_batch_window: int = NVIDIA_CONCURRENT_BATCH_WINDOW,
    on_call_result=None,
    cancel_check=None,
) -> None:
    """Fetches the source subtitle (via Bazarr's API, or from a local
    scratch-cache file when cached_source_path is provided — see
    engine.prefetch — never touches the media filesystem directly either
    way), translates it in batches sized to fit within the PRIMARY
    (cascade[0]) instance's context window (a full movie/episode's
    dialogue can easily exceed a small local model's limit if sent as one
    prompt — see srt_io.chunk_cues), reassembles each batch onto its
    original timing, and uploads the merged result back to Bazarr.
    Updates DB status throughout. cascade[0] is used only for batch
    sizing/log labeling here — the actual per-batch fallback walk through
    cascade[1:] happens inside _translate_batch."""
    item_id = item["id"]
    target_lang = item["target_language"]
    active_provider = cascade[0]

    resolved_batch_budget = _batch_token_budget(num_ctx, batch_token_budget_override)
    # Recorded on every item_run_log row so a later "why did this attempt
    # behave differently" question can be answered by reading the log
    # instead of cross-referencing timestamps against server restarts and
    # guessing which config was live at the time (see: the batch-size
    # regression that took multiple live runs to pin down).
    settings_snapshot = {
        "engine": active_provider.name,
        "num_ctx": num_ctx,
        "batch_token_budget_override": batch_token_budget_override,
        "resolved_batch_token_budget": resolved_batch_budget,
    }

    # Guard against translating into a language Bazarr already has a real
    # subtitle for — confirmed live: ~170 'done' items across 13 shows
    # (Stargate SG-1, Marshals, Fullmetal Alchemist, etc.) had
    # target_language set to a language Bazarr's wanted-list reported as
    # missing at SOME earlier poll (plausibly the "treat bundled
    # subtitles as downloaded" toggle transiently misreporting — see
    # app.engine.poller's own docstring), got dutifully translated into
    # by the LLM, and marked 'done' — even though Bazarr had a real,
    # already-downloaded subtitle in that language the whole time. Since
    # 'done' items are treated as a permanent record and survive every
    # future wanted-list purge by design, nothing ever caught this after
    # the fact; the language check surfaced it only because it happens to
    # re-verify against Bazarr's CURRENT state. Checking immediately
    # before translating (not just at poll time) catches a stale/wrong
    # wanted-list entry that slipped through, or a race where the file
    # appeared on Bazarr between the poll and this run. Marks the item
    # 'done' without spending an LLM call or touching Bazarr — nothing
    # needs uploading, since the language IS already present.
    if item["item_type"] == "episode":
        existing_detail = await client.get_episode_detail(item["bazarr_id"])
    else:
        existing_detail = await client.get_movie_detail(item["bazarr_id"])
    if existing_detail is not None:
        already_has_target = any(
            s.code2 == target_lang and s.path and not s.forced
            for s in existing_detail.subtitles
        )
        if already_has_target:
            logger.warning(
                "Item %d: Bazarr already has a real '%s' subtitle — skipping "
                "translation and marking done without uploading anything.",
                item_id, target_lang,
            )
            repository.update_item_status(
                conn, item_id, "done", source_language=source_lang, mark_completed=True,
            )
            repository.log_item_attempt(
                conn, item_id, run_id, "done",
                engine_used=None, model_used=None, settings_snapshot=settings_snapshot,
            )
            return

    repository.update_item_status(conn, item_id, "translating", mark_attempt=True)
    item_started = time.monotonic()

    try:
        fetch_started = time.monotonic()
        if cached_source_path is not None:
            original_subs = srt_io.parse_srt_bytes(cached_source_path.read_bytes())
        else:
            cues = await client.get_subtitle_contents(source_subtitle_path)
            original_subs = srt_io.cues_from_bazarr(cues)
        if not original_subs:
            raise ProviderError("Source subtitle has no cues")
        logger.info(
            "Item %d: source read+parse (cached=%s) took %.2fs, %d cues",
            item_id, cached_source_path is not None, time.monotonic() - fetch_started, len(original_subs),
        )

        chunk_started = time.monotonic()
        batches = srt_io.chunk_cues(original_subs, max_tokens_per_batch=resolved_batch_budget)
        logger.info(
            "Item %d: chunk_cues() took %.2fs, %d batches",
            item_id, time.monotonic() - chunk_started, len(batches),
        )

        catalan_vegeta_insults = repository.get_config(conn, "catalan_vegeta_insults", default=False)
        language_variants = repository.get_config(conn, "language_variants", default={})
        translate_started = time.monotonic()
        translated_subs, engine_used, model_used = await _translate_batches(
            batches, source_lang, target_lang, cascade, item_id,
            catalan_vegeta_insults, language_variants, retry_pause_seconds, run_id,
            concurrent_batch_window, on_call_result, cancel_check,
        )
        logger.info(
            "Item %d: all batches took %.2fs total (item started %.2fs ago)",
            item_id, time.monotonic() - translate_started, time.monotonic() - item_started,
        )

        # Full-file sanity check BEFORE the disclaimer is added (which
        # deliberately changes cue count/first-cue timing by design) — if
        # batching/reassembly silently dropped or misaligned a whole batch,
        # this catches it and blocks the upload entirely rather than
        # posting an incomplete or corrupted subtitle to Bazarr.
        verify_full_file_integrity(original_subs, translated_subs)

        if add_ai_disclaimer:
            disclaimer = srt_io.disclaimer_text(
                target_lang, language_name(source_lang), language_name(target_lang)
            )
            translated_subs = srt_io.with_ai_disclaimer(translated_subs, disclaimer)
        srt_bytes = srt_io.compose_srt(translated_subs)

        if queue_uploads:
            # Hold the translated file locally instead of uploading now —
            # Bazarr's own handling of the upload is what wakes the NAS's
            # disks, so queuing lets a whole run finish without touching
            # them; a later "push queued uploads" sends everything in one
            # burst. Status is NOT "done" — the upload itself hasn't
            # happened — but completed_at IS stamped here: translation
            # work genuinely finished at this moment, and the Queue page's
            # duration column needs it to show real per-item translation
            # time instead of "—" for every queued item.
            upload_queue.save_pending_upload(upload_queue.DEFAULT_QUEUE_ROOT, item_id, srt_bytes)
            repository.update_item_status(
                conn, item_id, "translated_pending_upload",
                source_language=source_lang, engine_used=engine_used, model_used=model_used,
                mark_completed=True,
            )
        else:
            if item["item_type"] == "episode":
                await client.upload_episode_subtitle(
                    series_id=item["series_id"],
                    episode_id=item["bazarr_id"],
                    language_code2=target_lang,
                    srt_bytes=srt_bytes,
                )
            else:
                await client.upload_movie_subtitle(
                    radarr_id=item["bazarr_id"],
                    language_code2=target_lang,
                    srt_bytes=srt_bytes,
                )

            repository.update_item_status(
                conn, item_id, "done",
                source_language=source_lang, engine_used=engine_used, model_used=model_used,
                mark_completed=True,
            )
        repository.log_item_attempt(
            conn, item_id, run_id, "done",
            engine_used=engine_used, model_used=model_used, settings_snapshot=settings_snapshot,
        )

    except (
        ProviderError,
        ProviderRateLimitedError,
        TranslationAlignmentError,
        TranslationIntegrityError,
    ) as exc:
        # engine_used=active_provider.name (not the possibly-unset local
        # `engine_used` from the try block, which only gets assigned on a
        # SUCCESSFUL _translate_batches() call) — a failure still happened
        # against a specific configured engine, and omitting it here meant
        # list_run_history's "WHERE engine_used IS NOT NULL" rollup query
        # silently dropped every failed-run row's engine entirely, showing
        # "primary_engine": null on the History page for any run that
        # failed outright (confirmed live: a run that failed on Groq showed
        # no engine at all on /history).
        repository.update_item_status(
            conn, item_id, "failed",
            error_message=str(exc), error_detail=getattr(exc, "raw_detail", None),
        )
        repository.log_item_attempt(
            conn, item_id, run_id, "failed",
            engine_used=active_provider.name, model_used=active_provider.model,
            error_message=str(exc), error_detail=getattr(exc, "raw_detail", None),
            settings_snapshot=settings_snapshot,
        )
        if run_id is not None:
            run_events.emit(run_id, item_id, 0, 0, "item_failed", str(exc))
        raise
    except RunCancelledError as exc:
        # A partial translation exists in memory at this point but is
        # deliberately discarded — reassembling/uploading a partially
        # translated file would silently ship an incomplete subtitle.
        # Marked 'failed' (not a new 'cancelled' status) so it surfaces on
        # the Queue/History pages and is re-runnable exactly like any
        # other failure, but with a message that makes clear this was a
        # deliberate Stop, not a real translation problem.
        repository.update_item_status(
            conn, item_id, "failed", error_message=str(exc),
        )
        repository.log_item_attempt(
            conn, item_id, run_id, "failed",
            engine_used=active_provider.name, model_used=active_provider.model,
            error_message=str(exc), settings_snapshot=settings_snapshot,
        )
        if run_id is not None:
            run_events.emit(run_id, item_id, 0, 0, "item_failed", str(exc))
        raise
    except Exception as exc:  # noqa: BLE001 - any unexpected failure must not crash the batch
        repository.update_item_status(
            conn, item_id, "failed",
            error_message=str(exc), error_detail=getattr(exc, "raw_detail", None),
        )
        repository.log_item_attempt(
            conn, item_id, run_id, "failed",
            engine_used=active_provider.name, model_used=active_provider.model,
            error_message=str(exc), error_detail=getattr(exc, "raw_detail", None),
            settings_snapshot=settings_snapshot,
        )
        if run_id is not None:
            run_events.emit(run_id, item_id, 0, 0, "item_failed", str(exc))
        raise
