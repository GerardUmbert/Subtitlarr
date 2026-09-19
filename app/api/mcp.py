import secrets

from fastapi import APIRouter, Depends

from app import state
from app.config import settings
from app.db import repository

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

_TOKEN_CONFIG_KEY = "mcp.auth_token"


def get_or_create_token(conn) -> str:
    """The MCP server's shared-secret bearer token, generated once on
    first access and persisted in app_config (same store as
    language_check_instance_id etc.) so it survives restarts. There is
    no rotation UI yet — regenerating is a deliberate, rare action (see
    POST /regenerate), not something that should ever happen silently."""
    token = repository.get_config(conn, _TOKEN_CONFIG_KEY, default=None)
    if token is None:
        token = secrets.token_urlsafe(32)
        repository.set_config(conn, _TOKEN_CONFIG_KEY, token)
    return token


@router.get("/status")
def get_mcp_status(conn=Depends(state.get_conn)):
    """Connection info for the Jobs page's MCP section — the token is
    shown in full here deliberately (same trust level as the Bazarr API
    key already shown in Settings): this is a local-admin-only page, not
    a multi-user product with different privilege levels."""
    return {
        "token": get_or_create_token(conn),
        "port": settings.mcp_port,
    }


@router.post("/regenerate-token")
def regenerate_token(conn=Depends(state.get_conn)):
    """Invalidates the old token immediately — any already-connected MCP
    client will start getting 401s and need its config updated with the
    new value. Use after a suspected leak, not routinely."""
    token = secrets.token_urlsafe(32)
    repository.set_config(conn, _TOKEN_CONFIG_KEY, token)
    return {"token": token}
