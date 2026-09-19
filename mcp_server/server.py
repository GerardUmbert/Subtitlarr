"""MCP tool definitions, mounted directly into the main FastAPI app at
/mcp (see app/main.py) rather than run as a separate process — see
plans/mcp-server.md for why. Every tool calls the same route-handler
functions app/api/*.py already exposes over HTTP, just in-process and
directly, with no HTTP round-trip and no separate auth/venv/port of its
own: whatever already protects access to this app's web UI/API protects
this too.

build_mcp() is a factory, not a module-level singleton — the `mcp`
Python SDK's StreamableHTTPSessionManager can only ever be `.run()`
once per instance ("Create a new instance if you need to run again"),
but Subtitlarr's own lifespan can start more than once in the same
process (every integration test's `with TestClient(app):` re-enters
it, and so does any hot-reload). A fresh FastMCP (and therefore a fresh
session manager) is built each time app/main.py's lifespan starts, via
create_mcp_asgi_app(), rather than reusing one built at import time.
"""
import functools
import inspect

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from app import state
from app.api import dashboard, engine_instances, history, jobs, languages, queue, run, schedule
from mcp_server.docs import DOC_LINKS
from mcp_server.safety import classify_failure


def _catch_http_errors(fn):
    """Route handlers in app/api/*.py raise HTTPException for expected
    failure cases (404 item not found, 409 run already active, 422 no
    usable source) — that's correct for FastAPI's own request handling,
    but calling these functions directly (not through a real request)
    means an uncaught HTTPException would surface to the MCP client as
    an opaque tool-call crash instead of the structured, informative
    error Subtitlarr's own web UI gets. Converts it into the same
    {"error": True, ...} shape a non-2xx HTTP response would have
    carried, for both sync and async handlers."""
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except HTTPException as exc:
                return {"error": True, "status_code": exc.status_code, "detail": exc.detail}
        return async_wrapper

    @functools.wraps(fn)
    def sync_wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except HTTPException as exc:
            return {"error": True, "status_code": exc.status_code, "detail": exc.detail}
    return sync_wrapper


READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False)
MUTATING = ToolAnnotations(readOnlyHint=False, destructiveHint=False)


def build_mcp() -> FastMCP:
    """Constructs a fresh FastMCP instance with every tool registered.
    Called once per app lifespan start (see app/main.py) — never reused
    across lifespans, see module docstring."""
    mcp = FastMCP(
        name="subtitlarr",
        # The SDK's default Host-header allowlist (localhost/127.0.0.1 only)
        # guards against DNS-rebinding attacks from a browser tab — not
        # relevant here, since every request already needs the bearer token
        # BearerAuthMiddleware checks before this ever runs (see
        # mcp_server/auth.py), and this server is reached over the LAN by
        # whatever IP each install happens to have, which can't be
        # allowlisted in advance. Disable it rather than guess hosts.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        instructions=(
            "Tools for Subtitlarr, a Bazarr-connected subtitle translation "
            "queue manager. Use the status/list tools to understand current "
            "state before triggering anything. IMPORTANT: never resubmit an "
            "item whose status is 'failed' back through run_by_ids/run_item "
            "unless subtitlarr_classify_failure says its error is 'retryable' "
            "— content-blocked, quota, and credential failures must instead "
            "go through the manual-translation tools, translated by you "
            "directly, never retried against the same engine."
        ),
        # Left at its default ("/mcp") deliberately — app/main.py mounts
        # this sub-app at the ASGI root ("") rather than at "/mcp" itself,
        # so this default path is what actually produces the final
        # "/mcp" endpoint. Overriding it to "/" here previously caused a
        # 307 redirect from "/mcp" to "/mcp/" (Starlette's
        # redirect_slashes kicking in on a mount whose only inner route
        # is exactly "/") — mounting at "" and keeping the SDK's own
        # "/mcp" default avoids that redirect entirely.
    )

    # -----------------------------------------------------------------
    # Documentation
    # -----------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    def subtitlarr_get_documentation_links() -> dict:
        """Returns links to Subtitlarr's own real documentation on
        GitHub (README, the full docs/settings reference site, install
        guide, engine/API-key setup, changelog, AGENTS.md, and the
        plans/ design-doc directory), each with a short description of
        what it covers. Use this before answering ANY "how do I
        configure/use/set up X" question about Subtitlarr itself, or
        before recommending a setting/workflow — fetch whichever
        linked page actually covers the topic and answer from that
        current, real content rather than from general knowledge,
        which may be stale or simply wrong about this specific app's
        current settings and defaults. This tool itself does not fetch
        anything — use your own web-fetch capability on the URL that's
        actually relevant."""
        return {"links": DOC_LINKS}

    # -----------------------------------------------------------------
    # Status / situational awareness
    # -----------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    def subtitlarr_get_dashboard_stats() -> dict:
        """Overall queue counts (pending/done/failed/etc.) — the same
        numbers Subtitlarr's Dashboard page shows."""
        return dashboard.get_stats(conn=state.get_conn())

    @mcp.tool(annotations=READ_ONLY)
    def subtitlarr_get_current_run() -> dict:
        """Whether a translation run is active right now, and its live
        progress (processed/failed/rate_per_min/eta_seconds). Check this
        before triggering any new run — Subtitlarr only allows one at a
        time."""
        return run.get_current(runner=state.get_runner())

    @mcp.tool(annotations=MUTATING)
    async def subtitlarr_poll_now() -> dict:
        """Refreshes queue counts from Bazarr's wanted list WITHOUT
        starting any translation — lets you see accurate current
        numbers before deciding whether/what to run. Check
        subtitlarr_get_poll_status afterward (it runs in the
        background) for when it finishes."""
        return await run.poll_now(runner=state.get_runner())

    @mcp.tool(annotations=READ_ONLY)
    def subtitlarr_get_poll_status() -> dict:
        """Whether the background refresh started by subtitlarr_poll_now
        is still running, and any error from the last one."""
        return run.poll_status()

    @mcp.tool(annotations=READ_ONLY)
    def subtitlarr_get_jobs_status() -> dict:
        """Combined status of every background job (scheduled translation,
        Bazarr media/subtitle sync, upload push, language check, backup,
        stale audit) — whether each is currently active, its cron schedule,
        and its last result/error. Use this for a single "what's going on
        right now" snapshot instead of separate calls."""
        jobs_info = jobs.get_jobs(
            conn=state.get_conn(), scheduler=state.get_scheduler(), runner=state.get_runner()
        )
        sync_status = jobs.get_sync_status()
        return {**jobs_info, **sync_status}

    @mcp.tool(annotations=READ_ONLY)
    def subtitlarr_list_queue(
        status: str | None = None,
        item_type: str | None = None,
        search: str | None = None,
        model: str | None = None,
        source_language: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """Lists queue items, same filters as the Queue page: status (e.g.
        'pending', 'failed', 'done', 'translating', 'translated_pending_upload'),
        item_type ('movie'/'episode'), search (title substring), model
        (model_used), source_language. Each row includes id, title, status,
        error_message, model_used, target_language — use error_message with
        subtitlarr_classify_failure before deciding whether a failed item is
        safe to retry."""
        return queue.list_queue(
            status=status, item_type=item_type, search=search, model=model,
            source_language=source_language, page=page, page_size=page_size,
            conn=state.get_conn(),
        )

    @mcp.tool(annotations=READ_ONLY)
    @_catch_http_errors
    def subtitlarr_get_item(item_id: int) -> dict:
        """Full detail for one queue item by id."""
        return queue.get_item(item_id, conn=state.get_conn())

    @mcp.tool(annotations=READ_ONLY)
    def subtitlarr_get_matching_count(
        status: str | None = None,
        item_type: str | None = None,
        search: str | None = None,
        model: str | None = None,
    ) -> dict:
        """How many currently-translatable items match a filter — check
        this before subtitlarr_run_filtered so you know the size of what
        you're about to kick off."""
        return queue.get_matching_count(
            status=status, item_type=item_type, search=search, model=model, conn=state.get_conn()
        )

    @mcp.tool(annotations=READ_ONLY)
    def subtitlarr_list_used_models() -> dict:
        """Every distinct model_used value seen across all items — e.g.
        to find everything translated by a specific (possibly weaker
        fallback) model, for subtitlarr_list_queue's model filter."""
        return queue.list_used_models(conn=state.get_conn())

    @mcp.tool(annotations=READ_ONLY)
    def subtitlarr_get_current_run_items() -> dict:
        """Every item touched by the currently-active run, in any status
        (queued/translating/done/failed) — the whole batch at once,
        rather than only whatever subtitlarr_list_queue's status filter
        happens to show. Returns active=false with no data if no run is
        active."""
        return queue.get_current_run_items(conn=state.get_conn(), runner=state.get_runner())

    @mcp.tool(annotations=READ_ONLY)
    def subtitlarr_list_history(page: int = 1, page_size: int = 20) -> dict:
        """Past translation runs — start/finish time, items
        processed/failed."""
        return history.list_history(page=page, page_size=page_size, conn=state.get_conn())

    @mcp.tool(annotations=READ_ONLY)
    @_catch_http_errors
    def subtitlarr_get_history_run_items(run_id: int) -> dict:
        """Every item that was part of one specific past run (by the id
        from subtitlarr_list_history) — for "why did run #42 fail on
        these items" after the fact, as opposed to
        subtitlarr_get_current_run_items which only covers a run that's
        active right now."""
        return history.get_history_run_items(run_id, conn=state.get_conn())

    @mcp.tool(annotations=READ_ONLY)
    def subtitlarr_get_run_events(since: int = 0) -> dict:
        """Live event feed for the currently-active run (retries,
        fallbacks, per-item failures) — pass the highest id you've
        already seen to get only newer events."""
        return run.get_run_events(since=since)

    @mcp.tool(annotations=READ_ONLY)
    def subtitlarr_get_history_events(
        after_id: int = 0,
        limit: int = 200,
        item_id: int | None = None,
        event_type: str | None = None,
        engine: str | None = None,
    ) -> dict:
        """The DURABLE per-item event log (survives restarts, unlike
        subtitlarr_get_run_events' in-memory live feed) — every retry,
        fallback, and failure ever recorded, optionally filtered to one
        item, one event_type, or one engine. Use this to investigate a
        specific item's full history, not just what's happening in a
        currently-active run."""
        return history.get_events(
            after_id=after_id, limit=limit, item_id=item_id, event_type=event_type, engine=engine,
        )

    @mcp.tool(annotations=READ_ONLY)
    def subtitlarr_get_job_events(limit: int = 100) -> dict:
        """Start/finish log of every scheduled job — Bazarr sync, source
        prefetch, upload push, language check, backup, stale audit, and
        scheduled translation runs — with status (running/done/failed)
        and result/error. Broader history than
        subtitlarr_get_jobs_status's live snapshot."""
        return history.get_job_events(limit=limit, conn=state.get_conn())

    @mcp.tool(annotations=READ_ONLY)
    def subtitlarr_list_language_mismatches(limit: int = 100) -> dict:
        """Items whose completed translation was found to actually still
        be in the source language (a well-formed but wrong-language
        response) — a real quality problem worth knowing about even
        though the item's own status may already show 'done'."""
        return history.get_language_mismatches(limit=limit, conn=state.get_conn())

    @mcp.tool(annotations=READ_ONLY)
    @_catch_http_errors
    def subtitlarr_get_history_stats(range: str = "all") -> dict:
        """Aggregate translation stats. range is one of '7d', '30d', 'all'."""
        return history.get_stats(range=range, conn=state.get_conn())

    # -----------------------------------------------------------------
    # Failure classification (the safety gate)
    # -----------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    def subtitlarr_classify_failure(error_message: str) -> dict:
        """Classifies a failed item's error_message as "retryable" (a
        transient provider-side hiccup — rate limit, timeout, 5xx — safe
        to resubmit via subtitlarr_run_by_ids/subtitlarr_run_item),
        "non_retryable" (content-blocked, quota-exhausted, or a
        dead/revoked credential — resubmitting to the SAME engine risks
        that provider's abuse enforcement against the account, up to
        suspension; must instead go through
        subtitlarr_get_manual_translation_source /
        subtitlarr_submit_manual_translation), or "unknown" (couldn't
        tell — MUST be treated the same as non_retryable: don't guess
        and resubmit)."""
        verdict = classify_failure(error_message)
        return {
            "verdict": verdict,
            "safe_to_retry_same_engine": verdict == "retryable",
        }

    # -----------------------------------------------------------------
    # Manual translation — the required path for non-retryable failures
    # -----------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    @_catch_http_errors
    async def subtitlarr_get_manual_translation_source(item_id: int) -> dict:
        """Resolves this item's source subtitle the normal way and
        returns its dialogue text as {item_id, source_language,
        target_language, cue_count, dialogue_text}. dialogue_text is cue
        blocks in "<index>\\n<content>" form separated by blank lines.
        Use this to translate an item yourself (the calling model) when
        its failure was classified non_retryable — see
        subtitlarr_classify_failure.

        Translate in chunks of ~250-350 cues for anything long, not the
        whole file in one pass. Every <index> in the source must appear
        exactly once in your output with the SAME index number —
        reassembly matches by index, not position, and a missing index
        silently falls back to the original-language text rather than
        erroring. Verify index parity (every 1..cue_count present
        exactly once) before calling
        subtitlarr_submit_manual_translation."""
        return await queue.get_manual_translation_source(
            item_id, conn=state.get_conn(), client=state.get_client()
        )

    @mcp.tool(annotations=MUTATING)
    @_catch_http_errors
    async def subtitlarr_submit_manual_translation(
        item_id: int, translated_text: str, model_name: str = "claude-code"
    ) -> dict:
        """Submits a translation produced by the calling model itself
        (NOT a configured provider) — runs it through the same
        reassembly, integrity-check, disclaimer, and upload pipeline a
        real provider's output goes through. No LLM call happens on the
        Subtitlarr side.

        translated_text must be the FULL combined output for every cue
        (all chunks concatenated), in the same "<index>\\n<content>"
        format returned by subtitlarr_get_manual_translation_source —
        one submission per item, not one call per chunk. engine_used is
        recorded as "manual" so this stays honestly distinguishable
        from a real API-driven translation everywhere in the UI."""
        req = queue.ManualTranslationRequest(translated_text=translated_text, model_name=model_name)
        return await queue.submit_manual_translation(
            item_id, req, conn=state.get_conn(), client=state.get_client(), runner=state.get_runner()
        )

    # -----------------------------------------------------------------
    # Run control
    # -----------------------------------------------------------------

    @mcp.tool(annotations=MUTATING)
    async def subtitlarr_run_now() -> dict:
        """Starts the same age-gated scheduled job on demand —
        translates up to today's remaining daily limit from the
        backlog, not necessarily everything. Returns {"started": false,
        "reason": ...} if a run is already active or the daily limit is
        already spent — check that reason rather than assuming
        success."""
        return await run.run_now(runner=state.get_runner())

    @mcp.tool(annotations=MUTATING)
    async def subtitlarr_run_filtered(
        status: str | None = None,
        item_type: str | None = None,
        search: str | None = None,
        model: str | None = None,
    ) -> dict:
        """Runs every translatable item matching a filter (same params
        as subtitlarr_list_queue) as one batch.

        HARD RULE: if status='failed' is part of this filter, first
        check each matching item's error_message with
        subtitlarr_classify_failure. Do not use this to blanket-retry a
        filtered set of failed items — a non_retryable item resubmitted
        here goes right back to the same engine that already rejected
        it. Prefer subtitlarr_run_by_ids with an explicitly vetted id
        list when any of the matches might be non-retryable."""
        return await queue.run_filtered(
            status=status, item_type=item_type, search=search, model=model,
            runner=state.get_runner(),
        )

    @mcp.tool(annotations=MUTATING)
    async def subtitlarr_run_by_ids(item_ids: list[int]) -> dict:
        """Runs an explicit set of item ids as one batch, through the
        normal configured engine cascade.

        HARD RULE: never include an id whose current status is 'failed'
        with a non_retryable error_message (per
        subtitlarr_classify_failure) — resubmitting content a provider
        already content-blocked, or hitting an already-exhausted/dead
        credential again, risks that provider's abuse enforcement
        against the account (up to suspension/termination), not just
        another failed item. Only rate-limited/transient failures are
        safe to include here. Non-retryable items belong in
        subtitlarr_get_manual_translation_source /
        subtitlarr_submit_manual_translation instead."""
        req = queue.RunByIdsRequest(item_ids=item_ids)
        return await queue.run_by_ids(req, runner=state.get_runner())

    @mcp.tool(annotations=MUTATING)
    @_catch_http_errors
    async def subtitlarr_run_item(item_id: int, force: bool = False) -> dict:
        """Runs a single item. Same hard rule as subtitlarr_run_by_ids:
        don't call this on an item whose last failure was classified
        non_retryable — use manual translation for those instead.
        force=true bypasses the normal "already done" skip, not the
        retry-safety rule above."""
        return await queue.run_item(
            item_id, force=force, conn=state.get_conn(), runner=state.get_runner(),
            client=state.get_client(),
        )

    @mcp.tool(annotations=MUTATING)
    def subtitlarr_cancel_run() -> dict:
        """Stops the active run after its in-flight item finishes (never
        mid-item). Remaining items are left untouched (pending/queued),
        not marked failed."""
        return run.cancel_current_run(runner=state.get_runner())

    # -----------------------------------------------------------------
    # Sync / jobs — low-risk, no LLM calls
    # -----------------------------------------------------------------

    @mcp.tool(annotations=MUTATING)
    async def subtitlarr_sync_media() -> dict:
        """Refreshes the wanted-subtitle list from Bazarr. No subtitle
        content is fetched, no translation runs."""
        return await jobs.sync_media(runner=state.get_runner())

    @mcp.tool(annotations=MUTATING)
    async def subtitlarr_sync_subs() -> dict:
        """Pre-fetches source subtitle content for pending items into
        the local cache. No translation, no uploads."""
        return await jobs.sync_subs(runner=state.get_runner())

    @mcp.tool(annotations=MUTATING)
    async def subtitlarr_push_uploads() -> dict:
        """Uploads every item currently held as
        translated_pending_upload to Bazarr in one pass."""
        return await jobs.push_uploads(conn=state.get_conn(), client=state.get_client())

    @mcp.tool(annotations=READ_ONLY)
    def subtitlarr_get_language_check_settings() -> dict:
        """Which engine instance (by id) the language check job uses,
        and how many completed items are still not yet checked. None
        means the check is disabled — it never runs until an instance
        is picked."""
        return jobs.get_language_check_settings(conn=state.get_conn())

    @mcp.tool(annotations=MUTATING)
    async def subtitlarr_set_language_check_instance(instance_id: int | None) -> dict:
        """Sets which engine instance the language check job uses — get
        the id from subtitlarr_list_engine_instances. Pass None to
        disable the check entirely (it will never run until an instance
        is set again)."""
        req = jobs.LanguageCheckSettings(instance_id=instance_id)
        return await jobs.set_language_check_settings(req, conn=state.get_conn())

    @mcp.tool(annotations=MUTATING)
    async def subtitlarr_run_language_check() -> dict:
        """Audits completed translations' actual output language
        against their target, catching a well-formed response that's
        silently still in the source language."""
        return await jobs.run_language_check_now(
            conn=state.get_conn(), client=state.get_client(), runner=state.get_runner()
        )

    @mcp.tool(annotations=MUTATING)
    async def subtitlarr_run_stale_audit() -> dict:
        """Checks every 'done' item against Bazarr's current subtitle
        state, resetting any found stale back to pending. No LLM
        involved."""
        return await jobs.run_stale_audit_now(
            conn=state.get_conn(), client=state.get_client(), runner=state.get_runner()
        )

    @mcp.tool(annotations=MUTATING)
    @_catch_http_errors
    async def subtitlarr_close_stale_runs() -> dict:
        """Closes out run_history rows left stuck "in progress" by a
        process that was killed mid-batch — marks them finished with
        their item counts backfilled, does not delete or change any
        item or its history. Also runs automatically on every server
        startup; this triggers it on demand. Blocked while a
        translation run is currently active."""
        return await jobs.close_stale_runs(conn=state.get_conn(), runner=state.get_runner())

    @mcp.tool(annotations=MUTATING)
    async def subtitlarr_clear_engine_rate_limits() -> dict:
        """Immediately un-flags every engine instance currently in a
        rate-limit/auth cooldown, all at once. Use after confirming
        (e.g. on the provider's own usage dashboard) an engine
        genuinely has headroom again, rather than waiting out the full
        cooldown — a still-exhausted engine just re-trips its cooldown
        after a few more failures, so this is safe to try speculatively."""
        return await jobs.clear_engine_rate_limits(conn=state.get_conn())

    # -----------------------------------------------------------------
    # Engine instances — list/reorder only, deliberately no credential
    # writes (adding or editing an instance's API key stays a UI-only
    # action; see plans/mcp-server.md)
    # -----------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    def subtitlarr_list_engine_instances() -> dict:
        """Every configured translation engine instance, in cascade
        order (the order a run tries them: primary first, then
        fallbacks). Each entry's id, name, provider_type, enabled, and
        rate-limit/cooldown state — never a real API key (masked as
        e.g. "sk-...ab" plus a has_api_key flag, same as the Engines
        page)."""
        return engine_instances.list_engine_instances(conn=state.get_conn())

    @mcp.tool(annotations=MUTATING)
    def subtitlarr_reorder_engine_instances(ids: list[int]) -> dict:
        """Sets the cascade order translation runs try engine instances
        in — the first id becomes primary, the rest are tried in order
        as fallbacks when an earlier one fails or is rate-limited. Pass
        every instance id (from subtitlarr_list_engine_instances) for a
        full, predictable reorder; any instance left out keeps its
        existing relative order, appended after the ones you did list —
        it is not removed or orphaned. Does not add, remove,
        enable/disable, or change credentials on any instance, only
        their relative order."""
        req = engine_instances.ReorderRequest(ids=ids)
        return engine_instances.reorder_engine_instances(req, conn=state.get_conn())

    # -----------------------------------------------------------------
    # Schedule / cutoff settings
    # -----------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    def subtitlarr_get_schedule_config() -> dict:
        """Current scheduling settings: the translation cron expression,
        age_threshold_days (how many days an item must have been
        missing a subtitle before a scheduled/"run now" pass will
        touch it — the "cutoff"), daily_translation_limit, and the
        independent sync/backup/telemetry cron expressions."""
        return schedule.get_schedule_config()

    @mcp.tool(annotations=MUTATING)
    @_catch_http_errors
    def subtitlarr_set_schedule_config(
        age_threshold_days: int | None = None,
        daily_translation_limit: int | None = None,
        cron_expression: str | None = None,
        pause_between_items_seconds: int | None = None,
    ) -> dict:
        """Updates one or more scheduling settings — any field left as
        None keeps its CURRENT value (this reads the full current
        config first and only overrides the fields you pass, so calling
        this with just age_threshold_days does not reset the cron
        expression or anything else back to a default). age_threshold_days
        is the "cutoff": lowering it (e.g. to 0) makes freshly-wanted
        items eligible for translation immediately instead of waiting,
        at the cost of giving Bazarr's own subtitle providers less time
        to find a real subtitle first. daily_translation_limit caps how
        many items a scheduled/"run now" pass will translate per day (0
        = unlimited); it does not apply to a forced per-item re-run or
        an explicit subtitlarr_run_by_ids call."""
        current = schedule.get_schedule_config()
        payload = schedule.ScheduleConfig(
            cron_expression=cron_expression if cron_expression is not None else current["cron_expression"],
            age_threshold_days=(
                age_threshold_days if age_threshold_days is not None else current["age_threshold_days"]
            ),
            daily_translation_limit=(
                daily_translation_limit
                if daily_translation_limit is not None
                else current["daily_translation_limit"]
            ),
            pause_between_items_seconds=(
                pause_between_items_seconds
                if pause_between_items_seconds is not None
                else current["pause_between_items_seconds"]
            ),
            clear_rate_limits_before_scheduled_run=current["clear_rate_limits_before_scheduled_run"],
            queue_uploads_enabled=current["queue_uploads_enabled"],
            push_uploads_cron=current["push_uploads_cron"],
            sync_media_cron=current["sync_media_cron"],
            sync_subs_cron=current["sync_subs_cron"],
            language_check_cron=current["language_check_cron"],
            backup_cron=current["backup_cron"],
            telemetry_enabled=current["telemetry_enabled"],
        )
        return schedule.set_schedule_config(
            payload, scheduler=state.get_scheduler(), conn=state.get_conn(), runner=state.get_runner()
        )

    # -----------------------------------------------------------------
    # Language rules — read-only. No target-language allowlist/source-
    # priority WRITE tool: those change what a scheduled run considers
    # translatable at all, closer to a deliberate configuration decision
    # than a routine action worth automating over MCP for now.
    # -----------------------------------------------------------------

    @mcp.tool(annotations=READ_ONLY)
    def subtitlarr_get_language_config() -> dict:
        """Current language rules: source_priority (which existing
        subtitle language to translate FROM when more than one is
        available), catalan_vegeta_insults (a Catalan-target-only
        toggle), language_variants (e.g. regional Spanish/Portuguese
        preferences), and target_language_allowlist (empty = no
        restriction; non-empty limits which of Bazarr's wanted target
        languages Subtitlarr will actually translate into)."""
        return languages.get_language_config(conn=state.get_conn())

    return mcp


def create_mcp_asgi_app():
    """Builds a fresh FastMCP instance and returns its mountable ASGI
    app — see module docstring for why this must be fresh per lifespan
    rather than a cached singleton. The returned Starlette app already
    carries its own lifespan (session_manager.run()); Starlette's Mount
    runs a sub-app's lifespan automatically, so app/main.py does not
    need to wire anything extra for this beyond mounting it."""
    return build_mcp().streamable_http_app()
