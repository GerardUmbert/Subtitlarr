from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import state
from app.bazarr.client import BazarrClient
from app.config import settings
from app.db import settings_store
from app.providers.languages import refresh_bazarr_names

router = APIRouter(prefix="/api/config/bazarr", tags=["bazarr"])


def _mask(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 6:
        return "***"
    return f"{secret[:3]}...{secret[-2:]}"


class BazarrConfig(BaseModel):
    base_url: str
    api_key: str | None = None  # only set to overwrite; omitted = unchanged


class BazarrTestRequest(BaseModel):
    base_url: str | None = None
    api_key: str | None = None  # blank/omitted = use the currently saved key


@router.get("")
async def get_bazarr_config():
    return {
        "base_url": settings.bazarr_base_url,
        "api_key_masked": _mask(settings.bazarr_api_key),
        "has_key": bool(settings.bazarr_api_key),
    }


@router.post("")
async def set_bazarr_config(config: BazarrConfig, conn=Depends(state.get_conn)):
    settings.bazarr_base_url = config.base_url
    settings_store.save_one(conn, "bazarr_base_url", config.base_url)
    if config.api_key:
        settings.bazarr_api_key = config.api_key
        settings_store.save_one(conn, "bazarr_api_key", config.api_key)
    if state.bazarr_client is not None:
        await state.bazarr_client.aclose()
    state.bazarr_client = BazarrClient(base_url=settings.bazarr_base_url, api_key=settings.bazarr_api_key)
    await refresh_bazarr_names(state.bazarr_client)
    return {"saved": True}


@router.post("/test")
async def test_bazarr_connection(req: BazarrTestRequest | None = None):
    """Tests the given (possibly unsaved) base_url/api_key without touching
    the saved config or the shared client — lets the UI verify a connection
    before committing to Save. Falls back to currently saved values for any
    field left blank, so testing after a partial edit still works."""
    base_url = (req.base_url if req and req.base_url else settings.bazarr_base_url)
    api_key = (req.api_key if req and req.api_key else settings.bazarr_api_key)

    if not base_url:
        return {"ok": False, "detail": "No Bazarr URL provided"}

    test_client = BazarrClient(base_url=base_url, api_key=api_key)
    try:
        ok = await test_client.test_connection()
    finally:
        await test_client.aclose()
    return {"ok": ok}
