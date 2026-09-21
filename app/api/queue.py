import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import state
from app.auth.session import require_session_or_mcp_token
from app.db import repository
from app.engine import manual_translation, prefetch, selector
from app.subtitles import srt_io

router = APIRouter(
    prefix="/api/queue", tags=["queue"],
    dependencies=[Depends(require_session_or_mcp_token)],
)

# Deliberately separate from `router` above — POST .../manual-translation
# accepts a short-lived, single-use, item-scoped upload token (see
# AUTH_PLAN.md, a local untracked design doc, and
# repository.consume_manual_translation_upload_token) as a full
# ALTERNATIVE to a real session or the standing MCP bearer token, not in
# addition to it. That token is specifically for a plain script (the one
# subtitlarr_submit_manual_translation's docstring tells the calling
# assistant to run) that has no reason to also hold the MCP token — the
# whole point of a scoped, disposable credential is to avoid that script
# needing a long-lived secret at all. A router-level dependency can't be
# selectively skipped for one route, hence the separate router.
manual_translation_router = APIRouter(prefix="/api/queue", tags=["queue"])


def _with_cached_flag(rows: list) -> list[dict]:
    """Whether each row's source subtitle is currently sitting in the local
    scratch cache — a pure filesystem fact (prefetch.py), not DB state, so
    it's checked live rather than stored. Cheap: just a path.exists() per
    row, no file reads."""
    result = []
    for row in rows:
        d = dict(row)
        d["source_cached_locally"] = (prefetch.DEFAULT_SCRATCH_ROOT / f"{d['id']}.srt").exists()
        result.append(d)
    return result


@router.get("")
def list_queue(
    status: str | None = None,
    item_type: str | None = None,
    search: str | None = None,
    exclude_no_source: bool = False,
    model: str | None = None,
    source_language: str | None = None,
    page: int = 1,
    page_size: int = 50,
    sort: str = "title",
    sort_by: str | None = None,
    sort_dir: str = "asc",
    conn=Depends(state.get_conn),
):
    with state.db_lock:
        rows, total = repository.list_queue(
            conn, status=status, item_type=item_type, search=search,
            exclude_no_source=exclude_no_source, model=model, source_language=source_language,
            page=page, page_size=page_size, sort=sort, sort_by=sort_by, sort_dir=sort_dir,
        )
    return {"data": _with_cached_flag(rows), "total": total, "page": page, "page_size": page_size}


@router.get("/models")
def list_used_models(conn=Depends(state.get_conn)):
    """Every distinct model_used value seen across all items — populates
    the Queue page's model filter chips."""
    with state.db_lock:
        return {"models": repository.list_used_models(conn)}


@router.get("/matching-count")
def get_matching_count(
    status: str | None = None,
    item_type: str | None = None,
    search: str | None = None,
    model: str | None = None,
    conn=Depends(state.get_conn),
):
    """How many currently-translatable (pending/queued/failed, or an
    explicitly-filtered status) items match this filter — used by the
    Queue page's 'Run all N matching' bulk action to show an accurate
    count before the user commits to it."""
    items = selector.get_filtered_translatable_queue(
        conn, status=status, item_type=item_type, search=search, model=model
    )
    return {"count": len(items)}


@router.post("/run-filtered")
async def run_filtered(
    status: str | None = None,
    item_type: str | None = None,
    search: str | None = None,
    model: str | None = None,
    runner=Depends(state.get_runner),
):
    """Runs every translatable item matching the given filter (same
    status/item_type/search/model params as GET /api/queue) — e.g. 'all TV',
    'everything matching a title search', 'everything a specific model
    translated' (re-run items produced by a weaker fallback model).
    Respects the normal daily cap/age gate, same as a scheduled run."""
    if runner.current is not None and runner.current.active:
        return {"started": False, "reason": "A run is already in progress"}
    remaining = runner.daily_limit_remaining()
    if remaining is not None and remaining <= 0:
        return {
            "started": False,
            "reason": f"Daily translation limit reached ({runner.daily_translation_limit}/day) "
            "— resets at UTC midnight, or raise the limit in Settings.",
        }
    state.spawn_background_task(
        runner.run_filtered(status, item_type, search, model), description="run-filtered"
    )
    return {"started": True}


class RunByIdsRequest(BaseModel):
    item_ids: list[int]


@router.post("/run-by-ids")
async def run_by_ids(req: RunByIdsRequest, runner=Depends(state.get_runner)):
    """Runs an explicit, caller-chosen set of item ids as ONE batch/
    run_history row — for selections the status/item_type/search filter
    params can't express, e.g. "every item currently translating INTO a
    specific target language," which isn't a filterable dimension on
    GET /api/queue at all. The caller (or an agent) is expected to have
    already resolved the exact id list it wants."""
    if runner.current is not None and runner.current.active:
        return {"started": False, "reason": "A run is already in progress"}
    if not req.item_ids:
        return {"started": False, "reason": "No item ids given"}
    state.spawn_background_task(runner.run_by_ids(req.item_ids), description="run-by-ids")
    return {"started": True, "count": len(req.item_ids)}


@router.get("/current-run")
def get_current_run_items(conn=Depends(state.get_conn), runner=Depends(state.get_runner)):
    """Every item touched by the currently-active run, in ANY status
    (queued/translating/done/failed) — the Queue page's 'current batch'
    view, so a running batch is visible as a whole rather than only
    through the regular status-filtered table."""
    progress = runner.current
    if progress is None or not progress.active or progress.run_id is None:
        return {"active": False, "data": []}
    with state.db_lock:
        rows = repository.list_items_by_ids(conn, progress.item_ids)
    return {"active": True, "run_id": progress.run_id, "data": _with_cached_flag(rows)}


@router.get("/{item_id}")
def get_item(item_id: int, conn=Depends(state.get_conn)):
    with state.db_lock:
        row = repository.get_item(conn, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return dict(row)


@router.post("/{item_id}/run")
async def run_item(
    item_id: int,
    force: bool = False,
    conn=Depends(state.get_conn),
    runner=Depends(state.get_runner),
    client=Depends(state.get_client),
):
    with state.db_lock:
        row = repository.get_item(conn, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Item not found")
    if runner.current is not None and runner.current.active:
        return {"started": False, "reason": "A run is already in progress"}

    # Re-resolve the source language fresh against Bazarr right now, rather
    # than trusting whatever was last recorded — the point of a manual
    # re-run is often exactly that something changed on Bazarr's end since
    # the last attempt/poll. This is purely for the immediate response
    # (so the UI can show an accurate "Translating from X to Y" toast);
    # run_single_item -> resolve_and_gate does its own independent
    # resolution right before actually translating.
    with state.db_lock:
        source_priority = repository.get_config(conn, "source_lang_priority", default=[])
    source_map = await selector.build_source_map(client, row["item_type"], row["bazarr_id"])
    resolved_source = selector.pick_source_language(
        source_map, row["target_language"], source_priority
    )

    state.spawn_background_task(
        runner.run_single_item(item_id, force_translate=force),
        description=f"run-single-item({item_id})",
    )
    return {"started": True, "source_language": resolved_source}


@router.get("/{item_id}/manual-translation/source")
async def get_manual_translation_source(
    item_id: int, conn=Depends(state.get_conn), client=Depends(state.get_client),
):
    """Resolves this item's source subtitle the same way a normal run
    would (source_lang_priority, HI-track fallback, etc.) and returns its
    dialogue text in the same "index\\ncontent" format a real translation
    provider is prompted with — for a human/external translator to
    translate by hand and post back via POST .../manual-translation.

    A no-usable-source or Bazarr-unreachable outcome marks the item
    skipped_no_source/failed respectively (same as any other resolution
    attempt) and is reported back as an error rather than silently
    returning nothing."""
    with state.db_lock:
        item = repository.get_item(conn, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    with state.db_lock:
        source_priority = repository.get_config(conn, "source_lang_priority", default=[])
    ready = await selector.resolve_and_gate(conn, client, [item], source_priority)
    if not ready:
        with state.db_lock:
            refreshed = repository.get_item(conn, item_id)
        raise HTTPException(
            status_code=422,
            detail=f"No usable source found — item status is now '{refreshed['status']}'.",
        )

    entry = ready[0]
    cues = await client.get_subtitle_contents(entry["source_path"])
    if not cues:
        raise HTTPException(status_code=422, detail="Source subtitle path resolved but has no cues.")

    subs = srt_io.cues_from_bazarr(cues)

    # Minted here (not only from the MCP tool) so a direct HTTP caller of
    # this GET gets the same token — the upload token is what
    # POST .../manual-translation accepts as a full alternative to a real
    # session/the MCP token, specifically so the plain submission script
    # subtitlarr_submit_manual_translation's docstring tells the calling
    # assistant to run doesn't need to also carry a standing credential.
    # 1 hour: generous enough that even a large multi-chunk translation
    # can finish before submission, still short-lived enough to be
    # worthless if it ends up echoed into a tool-call transcript later.
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    with state.db_lock:
        repository.create_manual_translation_upload_token(conn, token, item_id, expires_at)

    return {
        "item_id": item_id,
        "source_language": entry["source_lang"],
        "target_language": item["target_language"],
        "cue_count": len(subs),
        "dialogue_text": srt_io.extract_dialogue_text(subs),
        "upload_token": token,
    }


class ManualTranslationRequest(BaseModel):
    translated_text: str
    model_name: str = "claude-code"


def _require_upload_token_or_router_auth(request: Request, conn, item_id: int) -> None:
    """Auth for POST .../manual-translation specifically: a valid,
    unexpired, unused upload token scoped to THIS item_id (consumed
    atomically on success) OR the normal require_session_or_mcp_token
    check (a logged-in browser, or the standing MCP token) — either one
    is sufficient, not both. See manual_translation_router's own comment
    for why this route sits outside the blanket router-level dependency."""
    upload_token = request.headers.get("x-upload-token")
    if upload_token:
        with state.db_lock:
            consumed = repository.consume_manual_translation_upload_token(conn, upload_token, item_id)
        if consumed:
            return
        raise HTTPException(status_code=401, detail="Upload token is invalid, expired, or already used.")
    require_session_or_mcp_token(request, conn)


@manual_translation_router.post("/{item_id}/manual-translation")
async def submit_manual_translation(
    item_id: int, req: ManualTranslationRequest, request: Request,
    conn=Depends(state.get_conn), client=Depends(state.get_client), runner=Depends(state.get_runner),
):
    """Accepts a translation produced OUTSIDE any configured engine
    (e.g. by hand, or by an assistant reading source text from the GET
    endpoint above) and runs it through the same reassembly/integrity/
    disclaimer/upload/DB-tracking steps a real provider's output goes
    through — see engine.manual_translation.submit_manual_translation.
    No LLM call happens here at all.

    engine_used/model_used are recorded as "manual"/req.model_name (not
    a real provider name) so this is honestly distinguishable in the
    Queue's Model column, History, and the disclaimer line itself from
    an actual API-driven translation.

    Blocked while a translation run is active, same reasoning as the
    force-translate path — a live run could be mid-write on this exact
    item."""
    _require_upload_token_or_router_auth(request, conn, item_id)

    with state.db_lock:
        item = repository.get_item(conn, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    if runner.current is not None and runner.current.active:
        raise HTTPException(status_code=409, detail="A translation run is already in progress")

    with state.db_lock:
        source_priority = repository.get_config(conn, "source_lang_priority", default=[])
    ready = await selector.resolve_and_gate(conn, client, [item], source_priority)
    if not ready:
        with state.db_lock:
            refreshed = repository.get_item(conn, item_id)
        raise HTTPException(
            status_code=422,
            detail=f"No usable source found — item status is now '{refreshed['status']}'.",
        )
    entry = ready[0]

    try:
        result = await manual_translation.submit_manual_translation(
            conn, client, item, entry["source_lang"], entry["source_path"],
            req.translated_text, req.model_name,
        )
    except manual_translation.ManualTranslationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return result
