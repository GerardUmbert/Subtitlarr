import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.providers import pull_state
from app.providers.ollama_provider import OllamaProvider

router = APIRouter(prefix="/api/config/engines", tags=["engines"])

# Only the Ollama-specific model-management endpoints live here now —
# model listing/pulling talks to a specific Ollama SERVER, which isn't
# tied to any one engine_instances row (the same Ollama server could be
# used by several instances, or none yet, at the moment a user is
# picking a model for a not-yet-saved one). Everything else (per-
# provider-type config CRUD, cascade ordering, connection testing) moved
# to app/api/engine_instances.py when the old single active_engine/
# fallback_engine model was replaced by the ordered engine_instances
# list — see plans/multiple-engine-instances-cascade.md.


@router.get("/ollama/models")
async def list_ollama_models(base_url: str):
    """Lists models already pulled on the given Ollama server — for the
    Engines page's model picker on an Ollama instance card. base_url is
    required (no global settings.ollama_base_url to fall back to
    anymore); the UI always has a concrete URL to send since it's editing
    one specific instance's form."""
    provider = OllamaProvider(base_url=base_url, model="")
    try:
        models = await provider.list_models()
    except Exception as exc:  # noqa: BLE001 - surface to the UI, don't crash the app
        raise HTTPException(status_code=502, detail=f"Could not reach Ollama: {exc}") from exc
    finally:
        await provider.aclose()
    return {"models": models}


class PullModelRequest(BaseModel):
    base_url: str
    model: str


@router.post("/ollama/pull")
async def pull_ollama_model(req: PullModelRequest):
    if pull_state.current_pull.active:
        return {"started": False, "reason": "A model pull is already in progress"}

    provider = OllamaProvider(base_url=req.base_url, model=req.model)

    async def _run():
        try:
            await pull_state.run_pull(provider, req.model)
        finally:
            await provider.aclose()

    asyncio.create_task(_run())
    return {"started": True, "model": req.model}


@router.get("/ollama/pull")
async def get_pull_status():
    p = pull_state.current_pull
    return {
        "model": p.model,
        "active": p.active,
        "status": p.status,
        "completed": p.completed,
        "total": p.total,
        "pct": p.pct,
        "done": p.done,
        "error": p.error,
    }
