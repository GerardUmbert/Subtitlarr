from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app import state
from app.engine.compare import (
    CompareError,
    is_library_cached,
    parse_uploaded_srt,
    refresh_library_cache,
    run_compare,
    search_library,
)
from app.providers import languages as language_names
from app.providers import registry
from app.subtitles import srt_io

router = APIRouter(prefix="/api/compare", tags=["compare"])


@router.get("/languages")
def list_languages():
    """{code2: name} for every language Bazarr itself knows about (cached
    at startup via app.providers.languages.refresh_bazarr_names, same
    source translation prompts already use) — powers the compare tool's
    source/target-language pickers so they offer exactly the codes THIS
    Bazarr instance recognizes, not a generic/possibly-mismatched ISO
    list."""
    return {"languages": language_names.get_bazarr_language_list()}


@router.post("/library/refresh")
async def refresh_library(client=Depends(state.get_client)):
    """Fetches Bazarr's FULL episode+movie library into the compare tool's
    in-memory search cache — called once lazily by the first /library
    search if the cache is empty, or manually if the library changed
    since (new media added) and a search isn't finding it."""
    count = await refresh_library_cache(client)
    return {"cached": count}


@router.get("/library")
async def search_library_endpoint(
    q: str = "", source_language: str | None = None, client=Depends(state.get_client)
):
    """Searches Bazarr's full library (not Subtitlarr's own items table —
    that only holds items the poller found WANTED, a much narrower set)
    by title, optionally restricted to items with an existing subtitle in
    source_language. Lazily populates the cache on first call."""
    if not is_library_cached():
        await refresh_library_cache(client)
    return {"data": search_library(q, source_language)}


def _serialize(result):
    return {
        "run_id": result.run_id,
        "item_id": result.item_id,
        "source_lang": result.source_lang,
        "target_lang": result.target_lang,
        "parallel": result.parallel,
        "source_text": result.source_text,
        "results": [
            {
                "instance_id": r.instance_id,
                "instance_name": r.instance_name,
                "model": r.model,
                "ok": r.ok,
                "error": r.error,
                "subtitle_text": r.subtitle_text,
                "temperature": r.temperature,
                "total_seconds": round(r.total_seconds, 2),
                "batch_count": r.batch_count,
                "cue_count": r.cue_count,
                "avg_seconds_per_cue": round(r.avg_seconds_per_cue, 3),
            }
            for r in result.results
        ],
    }


class CompareRequest(BaseModel):
    item_type: str  # "episode" | "movie"
    bazarr_id: int
    source_language: str
    target_language: str
    instance_id_a: int
    # None runs ONLY instance_id_a — used by the "compare against an
    # uploaded reference translation" mode, where side B is a static file
    # rather than a second engine call.
    instance_id_b: int | None = None
    parallel: bool = False
    # Per-side override of the Catalan "Vegeta insults" style toggle —
    # None on either side falls back to the saved Language Rules setting.
    # Independent per side so the compare tool can actually show what the
    # toggle changes, instead of forcing both sides to match.
    catalan_vegeta_insults_a: bool | None = None
    catalan_vegeta_insults_b: bool | None = None
    # Per-side override of temperature — None on either side falls back to
    # that instance's own saved config value.
    temperature_a: float | None = None
    temperature_b: float | None = None


@router.post("")
async def compare(
    req: CompareRequest,
    conn=Depends(state.get_conn),
    client=Depends(state.get_client),
):
    """Source is an item picked from the full Bazarr library search (see
    GET /library) — an EXPLICITLY chosen source AND target language, not
    inherited from any existing Subtitlarr items row. See compare_uploaded
    below for the "upload your own .srt" source mode."""
    if req.instance_id_b is not None and req.instance_id_a == req.instance_id_b:
        raise HTTPException(status_code=400, detail="Pick two different engine instances to compare")
    try:
        registry.validate_temperature(req.temperature_a)
        registry.validate_temperature(req.temperature_b)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        result = await run_compare(
            conn, client,
            library_item_type=req.item_type, library_bazarr_id=req.bazarr_id,
            library_source_lang=req.source_language, library_target_lang=req.target_language,
            instance_id_a=req.instance_id_a, instance_id_b=req.instance_id_b,
            parallel=req.parallel,
            catalan_vegeta_insults_a=req.catalan_vegeta_insults_a,
            catalan_vegeta_insults_b=req.catalan_vegeta_insults_b,
            temperature_a=req.temperature_a,
            temperature_b=req.temperature_b,
        )
    except CompareError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _serialize(result)


@router.post("/uploaded")
async def compare_uploaded(
    source_file: UploadFile = File(...),
    source_lang: str = Form(...),
    target_lang: str = Form(...),
    instance_id_a: int = Form(...),
    instance_id_b: int | None = Form(None),
    parallel: bool = Form(False),
    catalan_vegeta_insults_a: bool | None = Form(None),
    catalan_vegeta_insults_b: bool | None = Form(None),
    temperature_a: float | None = Form(None),
    temperature_b: float | None = Form(None),
    conn=Depends(state.get_conn),
    client=Depends(state.get_client),
):
    """Source is a user-uploaded .srt file instead of a Bazarr item —
    never touches Bazarr, never requires the item to already be tracked
    in Subtitlarr's queue. source_lang/target_lang are supplied directly
    since there's no Bazarr item to infer them from."""
    if instance_id_b is not None and instance_id_a == instance_id_b:
        raise HTTPException(status_code=400, detail="Pick two different engine instances to compare")
    try:
        registry.validate_temperature(temperature_a)
        registry.validate_temperature(temperature_b)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    raw = await source_file.read()
    try:
        original_subs = parse_uploaded_srt(raw, label="source")
        result = await run_compare(
            conn, client,
            instance_id_a=instance_id_a, instance_id_b=instance_id_b, parallel=parallel,
            uploaded_source=original_subs, uploaded_source_lang=source_lang,
            uploaded_target_lang=target_lang,
            catalan_vegeta_insults_a=catalan_vegeta_insults_a,
            catalan_vegeta_insults_b=catalan_vegeta_insults_b,
            temperature_a=temperature_a,
            temperature_b=temperature_b,
        )
    except CompareError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _serialize(result)


@router.post("/reference")
async def parse_reference(reference_file: UploadFile = File(...)):
    """Parses an already-translated .srt you upload (e.g. exported from
    another tool, or one Bazarr already has) for the diff view, alongside
    a freshly-generated engine result — no engine call happens for this
    side, it's just displayed as-is."""
    raw = await reference_file.read()
    try:
        subs = parse_uploaded_srt(raw, label="reference translation")
    except CompareError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"subtitle_text": srt_io.compose_srt(subs).decode("utf-8")}
