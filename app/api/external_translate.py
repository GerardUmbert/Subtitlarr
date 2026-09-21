"""Lets any third-party caller (Bazarr, an MCP-connected assistant, or
another tool entirely) submit subtitle content directly for translation
by Subtitlarr's OWN configured engine cascade, with no Bazarr item/
wanted-list involvement at all — the caller owns delivering the result
back into whatever system needs it. The plain-HTTP entry points below
check a separate bearer token, not shared with either the MCP server's
own token (scoped for run-control/engine-cascade tool calls this surface
doesn't need) or Bazarr's own API key (that's Bazarr's outbound
credential to itself, not something Subtitlarr issues, so it can't
authenticate an incoming caller here). mcp_server/server.py's
subtitlarr_submit_external_translate/subtitlarr_get_external_translate_job
tools call submit_external_translate/get_external_translate_job directly
instead, skipping this token check — same reasoning as every other MCP
tool calling its route handler's underlying function in-process: the MCP
session's own bearer token already gates the whole connection."""
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
    result, a much narrower blast radius than the MCP tools.

    Locks around the DB access — the shared sqlite3.Connection isn't safe
    for concurrent cross-thread use without app.state.db_lock held around
    each call (see app.api.mcp.get_or_create_token's docstring for the
    live failure this was confirmed against: sqlite3.InterfaceError under
    real concurrent request traffic without this lock)."""
    with state.db_lock:
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
    with state.db_lock:
        repository.set_config(conn, _TOKEN_CONFIG_KEY, token)
    return {"token": token}


def _normalize_language_code(code: str) -> str:
    """Bare-code Bazarr convention (see app.bazarr.client.get_languages —
    even Bazarr's own non-standard codes like "pb" for Brazilian
    Portuguese are flat, never hyphenated) is what language_name(),
    disclaimer_text(), and language_variants are all keyed on throughout
    the rest of the pipeline. A caller sending a regional/locale-style
    code instead (e.g. "es-ES", "pt_BR") would otherwise silently miss
    every one of those lookups — language_name() falls back to just
    upper-casing the whole unrecognized string into the prompt instead of
    a real language name, and the disclaimer/variant lookups silently
    fall back to their defaults. Since neither Bazarr's exact behavior
    across every profile nor every other possible caller's convention is
    something this endpoint can assume, normalize defensively: take just
    the primary language subtag before the first "-"/"_", lowercased."""
    return code.replace("_", "-").split("-")[0].lower()


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

    @model_validator(mode="after")
    def _normalize_languages(self):
        self.source_language = _normalize_language_code(self.source_language)
        self.target_language = _normalize_language_code(self.target_language)
        if not self.source_language or not self.target_language:
            raise ValueError("source_language/target_language must not be empty.")
        return self


async def submit_external_translate(req: TranslateRequest, conn) -> dict:
    """The actual submit logic, factored out from the HTTP route so the
    MCP tool below can call it directly — same reasoning as every other
    MCP tool in mcp_server/server.py calling its route handler's
    underlying function in-process rather than over HTTP: the MCP bearer
    token already gates the whole session, so re-checking THIS surface's
    own token on top of that would just be a second, redundant, and here
    actually impossible check (there's no Authorization header to read on
    an in-process call). Accepts either Bazarr's own already-parsed cue
    format (the same SubtitleCue shape GET /api/subtitles/contents
    returns) or a plain raw .srt file's text, converts whichever was
    given to the same internal representation, and runs it through the
    normal chunking/cascade-translation pipeline as a background job.
    Also starts a job_events row (job='external_translate') so the
    attempt shows up on the History page's Jobs tab even for a caller
    with no other visibility into external_translate_jobs."""
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
    with state.db_lock:
        job_event_id = repository.start_job_event(conn, "external_translate", triggered_by="api")

    state.spawn_background_task(
        run_external_translate_job(
            conn, job_id, source_subs, req.source_language, req.target_language, job_event_id,
        ),
        description=f"external-translate-job({job_id})",
    )
    return {"job_id": job_id, "status": "pending"}


def get_external_translate_job(job_id: int, conn) -> dict:
    """Factored out from the HTTP route for the same reason as
    submit_external_translate above — reused directly by the MCP tool."""
    job = repository.get_external_translate_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("")
async def submit_external_translate_http(
    req: TranslateRequest,
    authorization: str | None = Header(default=None),
    conn=Depends(state.get_conn),
):
    """HTTP entry point — checks this surface's own bearer token (not the
    MCP token, not Bazarr's API key; see module docstring), then defers
    to submit_external_translate. Full-file translation can take minutes,
    so this returns a job id immediately rather than blocking — poll GET
    /{job_id} for the full result/error."""
    _require_auth(conn, authorization)
    return await submit_external_translate(req, conn)


@router.get("/{job_id}")
def get_external_translate_job_http(
    job_id: int,
    authorization: str | None = Header(default=None),
    conn=Depends(state.get_conn),
):
    _require_auth(conn, authorization)
    return get_external_translate_job(job_id, conn)
