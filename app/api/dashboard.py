from fastapi import APIRouter, Depends

from app import state
from app.db import repository

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/stats")
def get_stats(conn=Depends(state.get_conn)):
    return repository.get_stats(conn)
