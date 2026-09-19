"""MCP tool definitions — a thin adapter over Subtitlarr's existing REST
API (app/api/*.py). No tool here does anything Subtitlarr's own web UI
couldn't already do over the same endpoints; this just makes that
surface discoverable and typed for an MCP client. See
plans/mcp-server.md for the full design rationale.
"""
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from mcp_server.client import client
from mcp_server.config import config
from mcp_server.safety import classify_failure

mcp = FastMCP(
    name="subtitlarr",
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
    host=config.host,
    port=config.port,
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False)
MUTATING = ToolAnnotations(readOnlyHint=False, destructiveHint=False)


# ---------------------------------------------------------------------------
# Status / situational awareness
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
async def subtitlarr_get_dashboard_stats() -> dict:
    """Overall queue counts (pending/done/failed/etc.) — the same numbers
    Subtitlarr's Dashboard page shows."""
    return await client.get("/api/stats")


@mcp.tool(annotations=READ_ONLY)
async def subtitlarr_get_current_run() -> dict:
    """Whether a translation run is active right now, and its live
    progress (processed/failed/rate_per_min/eta_seconds). Check this
    before triggering any new run — Subtitlarr only allows one at a
    time."""
    return await client.get("/api/run/current")


@mcp.tool(annotations=READ_ONLY)
async def subtitlarr_get_jobs_status() -> dict:
    """Combined status of every background job (scheduled translation,
    Bazarr media/subtitle sync, upload push, language check, backup,
    stale audit) — whether each is currently active, its cron schedule,
    and its last result/error. Use this for a single "what's going on
    right now" snapshot instead of separate calls."""
    jobs_info = await client.get("/api/jobs")
    sync_status = await client.get("/api/jobs/sync-status")
    return {**jobs_info, **sync_status}


@mcp.tool(annotations=READ_ONLY)
async def subtitlarr_list_queue(
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
    return await client.get(
        "/api/queue",
        {
            "status": status, "item_type": item_type, "search": search, "model": model,
            "source_language": source_language, "page": page, "page_size": page_size,
        },
    )


@mcp.tool(annotations=READ_ONLY)
async def subtitlarr_get_item(item_id: int) -> dict:
    """Full detail for one queue item by id."""
    return await client.get(f"/api/queue/{item_id}")


@mcp.tool(annotations=READ_ONLY)
async def subtitlarr_get_matching_count(
    status: str | None = None,
    item_type: str | None = None,
    search: str | None = None,
    model: str | None = None,
) -> dict:
    """How many currently-translatable items match a filter — check this
    before subtitlarr_run_filtered so you know the size of what you're
    about to kick off."""
    return await client.get(
        "/api/queue/matching-count",
        {"status": status, "item_type": item_type, "search": search, "model": model},
    )


@mcp.tool(annotations=READ_ONLY)
async def subtitlarr_list_history(page: int = 1, page_size: int = 20) -> dict:
    """Past translation runs — start/finish time, items processed/failed."""
    return await client.get("/api/history", {"page": page, "page_size": page_size})


@mcp.tool(annotations=READ_ONLY)
async def subtitlarr_get_run_events(since: int = 0) -> dict:
    """Live event feed for the currently-active run (retries, fallbacks,
    per-item failures) — pass the highest id you've already seen to get
    only newer events."""
    return await client.get("/api/run/events", {"since": since})


@mcp.tool(annotations=READ_ONLY)
async def subtitlarr_list_language_mismatches(limit: int = 100) -> dict:
    """Items whose completed translation was found to actually still be
    in the source language (a well-formed but wrong-language response) —
    a real quality problem worth knowing about even though the item's
    own status may already show 'done'."""
    return await client.get("/api/history/language-mismatches", {"limit": limit})


@mcp.tool(annotations=READ_ONLY)
async def subtitlarr_get_history_stats(range: str = "all") -> dict:
    """Aggregate translation stats. range is one of '7d', '30d', 'all'."""
    return await client.get("/api/history/stats", {"range": range})


# ---------------------------------------------------------------------------
# Failure classification (the safety gate)
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
def subtitlarr_classify_failure(error_message: str) -> dict:
    """Classifies a failed item's error_message as "retryable" (a
    transient provider-side hiccup — rate limit, timeout, 5xx — safe to
    resubmit via subtitlarr_run_by_ids/subtitlarr_run_item),
    "non_retryable" (content-blocked, quota-exhausted, or a dead/revoked
    credential — resubmitting to the SAME engine risks that provider's
    abuse enforcement against the account, up to suspension; must
    instead go through subtitlarr_get_manual_translation_source /
    subtitlarr_submit_manual_translation), or "unknown" (couldn't tell —
    MUST be treated the same as non_retryable: don't guess and
    resubmit)."""
    verdict = classify_failure(error_message)
    return {
        "verdict": verdict,
        "safe_to_retry_same_engine": verdict == "retryable",
    }


# ---------------------------------------------------------------------------
# Manual translation — the required path for non-retryable failures
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
async def subtitlarr_get_manual_translation_source(item_id: int) -> dict:
    """Resolves this item's source subtitle the normal way and returns
    its dialogue text as {item_id, source_language, target_language,
    cue_count, dialogue_text}. dialogue_text is cue blocks in
    "<index>\\n<content>" form separated by blank lines. Use this to
    translate an item yourself (the calling model) when its failure was
    classified non_retryable — see subtitlarr_classify_failure.

    Translate in chunks of ~250-350 cues for anything long, not the
    whole file in one pass. Every <index> in the source must appear
    exactly once in your output with the SAME index number — reassembly
    matches by index, not position, and a missing index silently falls
    back to the original-language text rather than erroring. Verify
    index parity (every 1..cue_count present exactly once) before
    calling subtitlarr_submit_manual_translation."""
    return await client.get(f"/api/queue/{item_id}/manual-translation/source")


@mcp.tool(annotations=MUTATING)
async def subtitlarr_submit_manual_translation(
    item_id: int, translated_text: str, model_name: str = "claude-code"
) -> dict:
    """Submits a translation produced by the calling model itself (NOT a
    configured provider) — runs it through the same reassembly,
    integrity-check, disclaimer, and upload pipeline a real provider's
    output goes through. No LLM call happens on the Subtitlarr side.

    translated_text must be the FULL combined output for every cue
    (all chunks concatenated), in the same "<index>\\n<content>" format
    returned by subtitlarr_get_manual_translation_source — one
    submission per item, not one call per chunk. engine_used is recorded
    as "manual" so this stays honestly distinguishable from a real
    API-driven translation everywhere in the UI."""
    return await client.post(
        f"/api/queue/{item_id}/manual-translation",
        {"translated_text": translated_text, "model_name": model_name},
    )


# ---------------------------------------------------------------------------
# Run control
# ---------------------------------------------------------------------------


@mcp.tool(annotations=MUTATING)
async def subtitlarr_run_now() -> dict:
    """Starts the same age-gated scheduled job on demand — translates up
    to today's remaining daily limit from the backlog, not necessarily
    everything. Returns {"started": false, "reason": ...} if a run is
    already active or the daily limit is already spent — check that
    reason rather than assuming success."""
    return await client.post("/api/run/now")


@mcp.tool(annotations=MUTATING)
async def subtitlarr_run_filtered(
    status: str | None = None,
    item_type: str | None = None,
    search: str | None = None,
    model: str | None = None,
) -> dict:
    """Runs every translatable item matching a filter (same params as
    subtitlarr_list_queue) as one batch.

    HARD RULE: if status='failed' is part of this filter, first check
    each matching item's error_message with subtitlarr_classify_failure.
    Do not use this to blanket-retry a filtered set of failed items —
    a non_retryable item resubmitted here goes right back to the same
    engine that already rejected it. Prefer subtitlarr_run_by_ids with
    an explicitly vetted id list when any of the matches might be
    non-retryable."""
    return await client.post(
        "/api/queue/run-filtered",
        params={"status": status, "item_type": item_type, "search": search, "model": model},
    )


@mcp.tool(annotations=MUTATING)
async def subtitlarr_run_by_ids(item_ids: list[int]) -> dict:
    """Runs an explicit set of item ids as one batch, through the normal
    configured engine cascade.

    HARD RULE: never include an id whose current status is 'failed' with
    a non_retryable error_message (per subtitlarr_classify_failure) —
    resubmitting content a provider already content-blocked, or hitting
    an already-exhausted/dead credential again, risks that provider's
    abuse enforcement against the account (up to suspension/termination),
    not just another failed item. Only rate-limited/transient failures
    are safe to include here. Non-retryable items belong in
    subtitlarr_get_manual_translation_source /
    subtitlarr_submit_manual_translation instead."""
    return await client.post("/api/queue/run-by-ids", {"item_ids": item_ids})


@mcp.tool(annotations=MUTATING)
async def subtitlarr_run_item(item_id: int, force: bool = False) -> dict:
    """Runs a single item. Same hard rule as subtitlarr_run_by_ids: don't
    call this on an item whose last failure was classified
    non_retryable — use manual translation for those instead. force=true
    bypasses the normal "already done" skip, not the retry-safety rule
    above."""
    return await client.post(f"/api/queue/{item_id}/run", params={"force": force})


@mcp.tool(annotations=MUTATING)
async def subtitlarr_cancel_run() -> dict:
    """Stops the active run after its in-flight item finishes (never
    mid-item). Remaining items are left untouched (pending/queued), not
    marked failed."""
    return await client.post("/api/run/cancel")


# ---------------------------------------------------------------------------
# Sync / jobs — low-risk, no LLM calls
# ---------------------------------------------------------------------------


@mcp.tool(annotations=MUTATING)
async def subtitlarr_sync_media() -> dict:
    """Refreshes the wanted-subtitle list from Bazarr. No subtitle
    content is fetched, no translation runs."""
    return await client.post("/api/jobs/sync-media")


@mcp.tool(annotations=MUTATING)
async def subtitlarr_sync_subs() -> dict:
    """Pre-fetches source subtitle content for pending items into the
    local cache. No translation, no uploads."""
    return await client.post("/api/jobs/sync-subs")


@mcp.tool(annotations=MUTATING)
async def subtitlarr_push_uploads() -> dict:
    """Uploads every item currently held as translated_pending_upload to
    Bazarr in one pass."""
    return await client.post("/api/jobs/push-uploads")


@mcp.tool(annotations=MUTATING)
async def subtitlarr_run_language_check() -> dict:
    """Audits completed translations' actual output language against
    their target, catching a well-formed response that's silently still
    in the source language."""
    return await client.post("/api/jobs/language-check")


@mcp.tool(annotations=MUTATING)
async def subtitlarr_run_stale_audit() -> dict:
    """Checks every 'done' item against Bazarr's current subtitle state,
    resetting any found stale back to pending. No LLM involved."""
    return await client.post("/api/jobs/stale-audit")
