from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import state
from app.db import repository

router = APIRouter(prefix="/api/config/languages", tags=["languages"])


class LanguageConfig(BaseModel):
    source_priority: list[str]
    catalan_vegeta_insults: bool = False
    european_spanish: bool = True


@router.get("")
async def get_language_config(conn=Depends(state.get_conn)):
    return {
        "source_priority": repository.get_config(conn, "source_lang_priority", default=["en"]),
        "catalan_vegeta_insults": repository.get_config(conn, "catalan_vegeta_insults", default=False),
        "european_spanish": repository.get_config(conn, "european_spanish", default=True),
    }


@router.post("")
async def set_language_config(config: LanguageConfig, conn=Depends(state.get_conn)):
    repository.set_config(conn, "source_lang_priority", config.source_priority)
    repository.set_config(conn, "catalan_vegeta_insults", config.catalan_vegeta_insults)
    repository.set_config(conn, "european_spanish", config.european_spanish)
    return {"saved": True}
