from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import state
from app.db import repository
from app.engine import manual_translation, prefetch, selector
from app.subtitles import srt_io

router = APIRouter(prefix="/api/queue", tags=["queue"])


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
    rows = repository.list_items_by_ids(conn, progress.item_ids)
    return {"active": True, "run_id": progress.run_id, "data": _with_cached_flag(rows)}


@router.get("/{item_id}")
def get_item(item_id: int, conn=Depends(state.get_conn)):
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
    item = repository.get_item(conn, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")

    source_priority = repository.get_config(conn, "source_lang_priority", default=[])
    ready = await selector.resolve_and_gate(conn, client, [item], source_priority)
    if not ready:
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
    return {
        "item_id": item_id,
        "source_language": entry["source_lang"],
        "target_language": item["target_language"],
        "cue_count": len(subs),
        "dialogue_text": srt_io.extract_dialogue_text(subs),
    }


class ManualTranslationRequest(BaseModel):
    translated_text: str
    model_name: str = "claude-code"


@router.post("/{item_id}/manual-translation")
async def submit_manual_translation(
    item_id: int, req: ManualTranslationRequest,
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
    item = repository.get_item(conn, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    if runner.current is not None and runner.current.active:
        raise HTTPException(status_code=409, detail="A translation run is already in progress")

    source_priority = repository.get_config(conn, "source_lang_priority", default=[])
    ready = await selector.resolve_and_gate(conn, client, [item], source_priority)
    if not ready:
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
