"""Temporary read-only diagnostic endpoints — not linked from any nav page.
Added to investigate a real live incident (fansub-contaminated Italian
source picked over an embedded-only English track for "Georgie & Mandy's
First Marriage") without needing to hand the Bazarr API key to anyone
inspecting the issue. Safe to delete once the investigation is done."""

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app import state
from app.bazarr.client import BazarrClient
from app.db import engine_instances_repo

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.get("/gemini/{instance_id}/models")
async def list_gemini_models(
    instance_id: int, filter: str | None = None, conn=Depends(state.get_conn)
):
    """Lists the real model IDs Google's Gemini API exposes for the given
    instance's already-saved API key — the AI Studio dashboard shows
    friendly display names ("Gemma 4 26B") that don't map 1:1 to the
    actual API model string, so this hits Google's own ListModels
    endpoint directly instead of guessing. `filter` (optional,
    case-insensitive substring) narrows the result — e.g. filter=gemma.
    Reads the key server-side; never echoes it back. Safe to delete once
    no longer needed."""
    instance = engine_instances_repo.get_instance(conn, instance_id)
    if instance is None or instance["provider_type"] != "gemini":
        raise HTTPException(status_code=404, detail="Gemini instance not found")
    api_key = instance["config"].get("api_key")
    if not api_key:
        raise HTTPException(status_code=400, detail="This instance has no API key saved")

    async with httpx.AsyncClient(
        base_url="https://generativelanguage.googleapis.com/v1beta",
        headers={"x-goog-api-key": api_key},
        timeout=30.0,
    ) as client:
        resp = await client.get("/models")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Gemini API error: {resp.text}")

    models = resp.json().get("models", [])
    names = [m["name"].removeprefix("models/") for m in models]
    if filter:
        names = [n for n in names if filter.lower() in n.lower()]
    return {"count": len(names), "models": sorted(names)}


@router.get("/languages")
async def get_bazarr_languages(client: BazarrClient = Depends(state.get_client)):
    """Raw language list from Bazarr's own GET /api/system/languages —
    used to check what codes/names a real instance reports (e.g. Spanish
    regional variants) without hardcoding guesses. Safe to delete once no
    longer needed."""
    return await client.get_languages()



@router.get("/episode/{sonarr_episode_id}/detail")
async def get_episode_raw_detail(
    sonarr_episode_id: int, client: BazarrClient = Depends(state.get_client)
):
    """Raw EpisodeDetail from Bazarr — shows exactly what subtitles[] and
    missing_subtitles[] report, including embedded_track_id, so we can see
    whether Bazarr exposes an embedded-only track at all."""
    detail = await client.get_episode_detail(sonarr_episode_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Episode not found in Bazarr")
    return detail.model_dump()


@router.get("/episode/{sonarr_episode_id}/subtitle")
async def get_episode_subtitle_text(
    sonarr_episode_id: int,
    lang: str,
    client: BazarrClient = Depends(state.get_client),
):
    """Fetches the actual parsed cue content of whichever subtitle Bazarr
    currently has on disk for this episode+language (e.g. the ES subtitle
    Subtitlarr already uploaded) — read-only, no upload/write."""
    detail = await client.get_episode_detail(sonarr_episode_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Episode not found in Bazarr")
    match = next((s for s in detail.subtitles if s.code2 == lang and s.path), None)
    if match is None:
        raise HTTPException(
            status_code=404,
            detail=f"No '{lang}' subtitle with a real path found; available: "
            f"{[(s.code2, s.path) for s in detail.subtitles]}",
        )
    cues = await client.get_subtitle_contents(match.path)
    return {
        "path": match.path,
        "cue_count": len(cues),
        "first_20_cues": [c.content for c in cues[:20]],
    }


@router.get("/movie/{radarr_id}/subtitle")
async def get_movie_subtitle_text(
    radarr_id: int,
    lang: str,
    client: BazarrClient = Depends(state.get_client),
):
    """Movie counterpart to the episode subtitle debug route above — same
    read-only fetch, just against get_movie_detail instead."""
    detail = await client.get_movie_detail(radarr_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Movie not found in Bazarr")
    match = next((s for s in detail.subtitles if s.code2 == lang and s.path), None)
    if match is None:
        raise HTTPException(
            status_code=404,
            detail=f"No '{lang}' subtitle with a real path found; available: "
            f"{[(s.code2, s.path) for s in detail.subtitles]}",
        )
    cues = await client.get_subtitle_contents(match.path)
    return {
        "path": match.path,
        "cue_count": len(cues),
        "cues": [
            {"start": str(c.start), "end": str(c.end), "content": c.content}
            for c in cues
        ],
    }
