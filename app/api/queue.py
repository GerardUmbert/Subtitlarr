import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app import state
from app.db import repository
from app.engine import prefetch, selector

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
async def list_queue(
    status: str | None = None,
    item_type: str | None = None,
    search: str | None = None,
    exclude_no_source: bool = False,
    page: int = 1,
    page_size: int = 50,
    sort: str = "title",
    conn=Depends(state.get_conn),
):
    rows, total = repository.list_queue(
        conn, status=status, item_type=item_type, search=search,
        exclude_no_source=exclude_no_source, page=page, page_size=page_size, sort=sort,
    )
    return {"data": _with_cached_flag(rows), "total": total, "page": page, "page_size": page_size}


@router.get("/matching-count")
async def get_matching_count(
    status: str | None = None,
    item_type: str | None = None,
    search: str | None = None,
    conn=Depends(state.get_conn),
):
    """How many currently-translatable (pending/queued/failed, or an
    explicitly-filtered status) items match this filter — used by the
    Queue page's 'Run all N matching' bulk action to show an accurate
    count before the user commits to it."""
    items = selector.get_filtered_translatable_queue(
        conn, status=status, item_type=item_type, search=search
    )
    return {"count": len(items)}


@router.post("/run-filtered")
async def run_filtered(
    status: str | None = None,
    item_type: str | None = None,
    search: str | None = None,
    runner=Depends(state.get_runner),
):
    """Runs every translatable item matching the given filter (same
    status/item_type/search params as GET /api/queue) — e.g. 'all TV',
    'everything matching a title search'. Respects the normal daily
    cap/age gate, same as a scheduled run."""
    if runner.current is not None and runner.current.active:
        return {"started": False, "reason": "A run is already in progress"}
    asyncio.create_task(runner.run_filtered(status, item_type, search))
    return {"started": True}


@router.get("/current-run")
async def get_current_run_items(conn=Depends(state.get_conn), runner=Depends(state.get_runner)):
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
async def get_item(item_id: int, conn=Depends(state.get_conn)):
    row = repository.get_item(conn, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return dict(row)


@router.post("/{item_id}/run")
async def run_item(
    item_id: int,
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

    asyncio.create_task(runner.run_single_item(item_id))
    return {"started": True, "source_language": resolved_source}
