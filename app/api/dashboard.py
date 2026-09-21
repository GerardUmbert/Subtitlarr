from fastapi import APIRouter, Depends

from app import state
from app.auth.session import require_session_or_mcp_token
from app.db import repository

router = APIRouter(
    prefix="/api", tags=["dashboard"],
    dependencies=[Depends(require_session_or_mcp_token)],
)


@router.get("/stats")
def get_stats(conn=Depends(state.get_conn)):
    with state.db_lock:
        return repository.get_stats(conn)
