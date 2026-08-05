from fastapi import APIRouter, Depends, HTTPException

from app import state
from app.db import repository

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("")
async def list_history(page: int = 1, page_size: int = 20, conn=Depends(state.get_conn)):
    runs, total = repository.list_run_history(conn, page=page, page_size=page_size)
    return {"data": runs, "total": total, "page": page, "page_size": page_size}


@router.get("/{run_id}/items")
async def get_history_run_items(run_id: int, conn=Depends(state.get_conn)):
    row = conn.execute("SELECT id FROM run_history WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"data": repository.get_run_items(conn, run_id)}
