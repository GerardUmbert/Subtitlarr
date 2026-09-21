from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app import state
from app.auth.session import require_session_or_mcp_token
from app.db import repository
from app.providers.prompts import DEFAULT_LANGUAGE_VARIANTS, LANGUAGE_VARIANTS

router = APIRouter(
    prefix="/api/config/languages", tags=["languages"],
    dependencies=[Depends(require_session_or_mcp_token)],
)


class LanguageConfig(BaseModel):
    source_priority: list[str]
    catalan_vegeta_insults: bool = False
    language_variants: dict[str, str] = {}
    # Empty = no restriction, same "preference not requirement" default as
    # source_priority — Bazarr's wanted-language profile is still the only
    # thing that decides what's missing. Non-empty restricts which of
    # THOSE wanted languages Subtitlarr will actually create/run a
    # translation job for, so a Bazarr profile can keep wanting a
    # fallback source language (e.g. EN) without Subtitlarr ever
    # translating INTO it.
    target_language_allowlist: list[str] = []


@router.get("")
def get_language_config(conn=Depends(state.get_conn)):
    with state.db_lock:
        source_priority = repository.get_config(conn, "source_lang_priority", default=["en"])
    with state.db_lock:
        catalan_vegeta_insults = repository.get_config(conn, "catalan_vegeta_insults", default=False)
    with state.db_lock:
        language_variants = repository.get_config(conn, "language_variants", default={})
    with state.db_lock:
        target_language_allowlist = repository.get_config(conn, "target_lang_allowlist", default=[])
    return {
        "source_priority": source_priority,
        "catalan_vegeta_insults": catalan_vegeta_insults,
        "language_variants": language_variants,
        "target_language_allowlist": target_language_allowlist,
    }


@router.get("/variants")
def get_available_variants():
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
def set_language_config(config: LanguageConfig, conn=Depends(state.get_conn)):
    with state.db_lock:
        repository.set_config(conn, "source_lang_priority", config.source_priority)
    with state.db_lock:
        repository.set_config(conn, "catalan_vegeta_insults", config.catalan_vegeta_insults)
    with state.db_lock:
        repository.set_config(conn, "language_variants", config.language_variants)
    with state.db_lock:
        repository.set_config(conn, "target_lang_allowlist", config.target_language_allowlist)
    return {"saved": True}
