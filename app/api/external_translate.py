"""Lets any third-party caller (Bazarr, or another tool entirely) submit
subtitle content directly for translation, with no Bazarr item/wanted-list
involvement at all — the caller owns delivering the result back into
whatever system needs it. Separate bearer-token auth from both the MCP
server's token (scoped for run-control/engine-cascade tool calls this
surface doesn't need) and Bazarr's own API key (that's Bazarr's outbound
credential to itself, not something Subtitlarr issues, so it can't
authenticate an incoming caller here)."""
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, model_validator

from app import state
from app.bazarr.schemas import SubtitleCue
from app.db import repository
from app.engine.external_translate import run_external_translate_job
from app.subtitles import srt_io

router = APIRouter(prefix="/api/external-translate", tags=["external-translate"])

_TOKEN_CONFIG_KEY = "external_translate.auth_token"


def get_or_create_token(conn) -> str:
    """Same generate-once-and-persist pattern as the MCP server's token
    (app.api.mcp) — a separate token, not shared with it, since this
    surface's only capability is submitting content and reading back a
    result, a much narrower blast radius than the MCP tools."""
    token = repository.get_config(conn, _TOKEN_CONFIG_KEY, default=None)
    if token is None:
        token = secrets.token_urlsafe(32)
        repository.set_config(conn, _TOKEN_CONFIG_KEY, token)
    return token


def _require_auth(conn, authorization: str | None) -> None:
    expected = f"Bearer {get_or_create_token(conn)}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/status")
def get_status(conn=Depends(state.get_conn)):
    """Connection info for a future settings-page section — same trust
    level as the MCP token/Bazarr API key already shown there (local-admin
    page, not a multi-user product)."""
    return {"token": get_or_create_token(conn)}


@router.post("/regenerate-token")
def regenerate_token(conn=Depends(state.get_conn)):
    token = secrets.token_urlsafe(32)
    repository.set_config(conn, _TOKEN_CONFIG_KEY, token)
    return {"token": token}


class TranslateRequest(BaseModel):
    source_language: str
    target_language: str
    cues: list[SubtitleCue] | None = None
    srt_content: str | None = None

    @model_validator(mode="after")
    def _exactly_one_input(self):
        if (self.cues is None) == (self.srt_content is None):
            raise ValueError("Provide exactly one of 'cues' or 'srt_content', not both/neither.")
        return self


@router.post("")
async def submit_external_translate(
    req: TranslateRequest,
    authorization: str | None = Header(default=None),
    conn=Depends(state.get_conn),
):
    """Accepts either Bazarr's own already-parsed cue format (the same
    SubtitleCue shape GET /api/subtitles/contents returns — no reason to
    make Bazarr re-serialize to raw SRT text when it already has this) or
    a plain raw .srt file's text, converts whichever was given to the same
    internal representation, and runs it through the normal chunking/
    cascade-translation pipeline as a background job. Full-file translation
    can take minutes, so this returns a job id immediately rather than
    blocking — poll GET /{job_id} for the result."""
    _require_auth(conn, authorization)

    if req.cues is not None:
        source_subs = srt_io.cues_from_bazarr(req.cues)
    else:
        try:
            source_subs = srt_io.parse_srt_bytes(req.srt_content.encode("utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Could not parse srt_content: {exc}")

    if not source_subs:
        raise HTTPException(status_code=422, detail="No cues found in the submitted content.")

    with state.db_lock:
        job_id = repository.create_external_translate_job(
            conn, req.source_language, req.target_language,
        )

    state.spawn_background_task(
        run_external_translate_job(conn, job_id, source_subs, req.source_language, req.target_language),
        description=f"external-translate-job({job_id})",
    )
    return {"job_id": job_id, "status": "pending"}


@router.get("/{job_id}")
def get_external_translate_job_status(
    job_id: int,
    authorization: str | None = Header(default=None),
    conn=Depends(state.get_conn),
):
    _require_auth(conn, authorization)
    job = repository.get_external_translate_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
