from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import state
from app.db import repository

router = APIRouter(prefix="/api/config/languages", tags=["languages"])


class LanguageConfig(BaseModel):
    source_priority: list[str]
    managed_languages: list[str] = []


@router.get("")
async def get_language_config(conn=Depends(state.get_conn)):
    return {
        "source_priority": repository.get_config(conn, "source_lang_priority", default=[]),
        "managed_languages": repository.get_config(conn, "managed_languages", default=[]),
    }


@router.post("")
async def set_language_config(config: LanguageConfig, conn=Depends(state.get_conn)):
    repository.set_config(conn, "source_lang_priority", config.source_priority)
    repository.set_config(conn, "managed_languages", config.managed_languages)
    return {"saved": True}
