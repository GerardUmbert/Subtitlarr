from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import state
from app.db import repository
from app.providers.prompts import DEFAULT_LANGUAGE_VARIANTS, LANGUAGE_VARIANTS

router = APIRouter(prefix="/api/config/languages", tags=["languages"])


class LanguageConfig(BaseModel):
    source_priority: list[str]
    catalan_vegeta_insults: bool = False
    language_variants: dict[str, str] = {}


@router.get("")
async def get_language_config(conn=Depends(state.get_conn)):
    return {
        "source_priority": repository.get_config(conn, "source_lang_priority", default=["en"]),
        "catalan_vegeta_insults": repository.get_config(conn, "catalan_vegeta_insults", default=False),
        "language_variants": repository.get_config(conn, "language_variants", default={}),
    }


@router.get("/variants")
async def get_available_variants():
    """The full LANGUAGE_VARIANTS registry (labels only, not the prompt
    addon text) plus each language's default — powers the Language Rules
    page's per-language variant dropdowns without hardcoding the options
    client-side."""
    return {
        "variants": {
            lang: {key: label for key, (label, _addon) in options.items()}
            for lang, options in LANGUAGE_VARIANTS.items()
        },
        "defaults": DEFAULT_LANGUAGE_VARIANTS,
    }


@router.post("")
async def set_language_config(config: LanguageConfig, conn=Depends(state.get_conn)):
    repository.set_config(conn, "source_lang_priority", config.source_priority)
    repository.set_config(conn, "catalan_vegeta_insults", config.catalan_vegeta_insults)
    repository.set_config(conn, "language_variants", config.language_variants)
    return {"saved": True}
