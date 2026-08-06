"""Temporary read-only diagnostic endpoints — not linked from any nav page.
Added to investigate a real live incident (fansub-contaminated Italian
source picked over an embedded-only English track for "Georgie & Mandy's
First Marriage") without needing to hand the Bazarr API key to anyone
inspecting the issue. Safe to delete once the investigation is done."""

from fastapi import APIRouter, Depends, HTTPException

from app import state
from app.bazarr.client import BazarrClient

router = APIRouter(prefix="/api/debug", tags=["debug"])


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
