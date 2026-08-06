from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException

from app import state
from app.db import repository
from app.engine import log_events, stats

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("")
async def list_history(
    page: int = 1,
    page_size: int = 20,
    sort_by: str | None = None,
    sort_dir: str = "desc",
    conn=Depends(state.get_conn),
):
    runs, total = repository.list_run_history(
        conn, page=page, page_size=page_size, sort_by=sort_by, sort_dir=sort_dir
    )
    return {"data": runs, "total": total, "page": page, "page_size": page_size}


@router.get("/{run_id}/items")
async def get_history_run_items(run_id: int, conn=Depends(state.get_conn)):
    row = conn.execute("SELECT id FROM run_history WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"data": repository.get_run_items(conn, run_id)}


@router.get("/events")
async def get_events(
    after_id: int = 0,
    limit: int = 200,
    item_id: int | None = None,
    event_type: str | None = None,
    engine: str | None = None,
    sort_by: str | None = None,
    sort_dir: str = "desc",
):
    events = log_events.read_events(
        after_id=after_id, limit=limit, item_id=item_id, event_type=event_type, engine=engine,
        sort_by=sort_by, sort_dir=sort_dir,
    )
    return {"data": [asdict(e) for e in events], "latest_id": log_events.latest_line_id()}


@router.get("/stats")
async def get_stats(range: str = "all", conn=Depends(state.get_conn)):
    if range not in ("7d", "30d", "all"):
        raise HTTPException(status_code=400, detail="range must be one of: 7d, 30d, all")
    return stats.compute_stats(conn, range_=range)
