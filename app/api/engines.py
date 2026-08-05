import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import state
from app.config import settings
from app.db import settings_store
from app.providers import pull_state
from app.providers.gemini_provider import GeminiProvider
from app.providers.nvidia_provider import NvidiaProvider
from app.providers.ollama_provider import OllamaProvider

router = APIRouter(prefix="/api/config/engines", tags=["engines"])


def _mask(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 6:
        return "***"
    return f"{secret[:3]}...{secret[-2:]}"


class EngineConfig(BaseModel):
    active_engine: str
    fallback_engine: str = ""
    ollama_base_url: str
    ollama_model: str
    ollama_num_ctx: int = 8192
    ollama_batch_token_budget: int = 0
    gemini_model: str
    gemini_api_key: str | None = None  # only set to overwrite; omitted = unchanged
    nvidia_model: str = "deepseek-ai/deepseek-v4-flash"
    nvidia_api_key: str | None = None  # only set to overwrite; omitted = unchanged
    nvidia_batch_token_budget: int = 2000


@router.get("")
async def get_engine_config():
    return {
        "active_engine": settings.active_engine,
        "fallback_engine": settings.fallback_engine,
        "ollama_base_url": settings.ollama_base_url,
        "ollama_model": settings.ollama_model,
        "ollama_num_ctx": settings.ollama_num_ctx,
        "ollama_batch_token_budget": settings.ollama_batch_token_budget,
        "gemini_model": settings.gemini_model,
        "gemini_api_key_masked": _mask(settings.gemini_api_key),
        "gemini_has_key": bool(settings.gemini_api_key),
        "nvidia_model": settings.nvidia_model,
        "nvidia_api_key_masked": _mask(settings.nvidia_api_key),
        "nvidia_has_key": bool(settings.nvidia_api_key),
        "nvidia_batch_token_budget": settings.nvidia_batch_token_budget,
    }


@router.post("")
async def set_engine_config(config: EngineConfig, conn=Depends(state.get_conn)):
    if config.ollama_num_ctx < 512:
        raise HTTPException(status_code=422, detail="ollama_num_ctx must be at least 512")
    if config.ollama_batch_token_budget < 0:
        raise HTTPException(status_code=422, detail="ollama_batch_token_budget must be >= 0")
    if config.nvidia_batch_token_budget < 400:
        raise HTTPException(status_code=422, detail="nvidia_batch_token_budget must be at least 400")
    settings.active_engine = config.active_engine
    settings_store.save_one(conn, "active_engine", config.active_engine)
    settings.fallback_engine = config.fallback_engine
    settings_store.save_one(conn, "fallback_engine", config.fallback_engine)
    settings.ollama_base_url = config.ollama_base_url
    settings_store.save_one(conn, "ollama_base_url", config.ollama_base_url)
    settings.ollama_model = config.ollama_model
    settings_store.save_one(conn, "ollama_model", config.ollama_model)
    settings.ollama_num_ctx = config.ollama_num_ctx
    settings_store.save_one(conn, "ollama_num_ctx", config.ollama_num_ctx)
    settings.ollama_batch_token_budget = config.ollama_batch_token_budget
    settings_store.save_one(conn, "ollama_batch_token_budget", config.ollama_batch_token_budget)
    settings.gemini_model = config.gemini_model
    settings_store.save_one(conn, "gemini_model", config.gemini_model)
    if config.gemini_api_key:
        settings.gemini_api_key = config.gemini_api_key
        settings_store.save_one(conn, "gemini_api_key", config.gemini_api_key)
    settings.nvidia_model = config.nvidia_model
    settings_store.save_one(conn, "nvidia_model", config.nvidia_model)
    if config.nvidia_api_key:
        settings.nvidia_api_key = config.nvidia_api_key
        settings_store.save_one(conn, "nvidia_api_key", config.nvidia_api_key)
    settings.nvidia_batch_token_budget = config.nvidia_batch_token_budget
    settings_store.save_one(conn, "nvidia_batch_token_budget", config.nvidia_batch_token_budget)
    return {"saved": True}


class TestEngineRequest(BaseModel):
    base_url: str | None = None  # ollama only; blank = use currently saved value
    model: str | None = None  # blank = use currently saved value
    api_key: str | None = None  # gemini/nvidia only; blank = use currently saved value


@router.post("/{name}/test")
async def test_engine(name: str, req: TestEngineRequest | None = None):
    """Tests the given (possibly unsaved) form values directly, without
    persisting them — mirrors /api/config/bazarr/test. Falls back to saved
    settings for any field left blank."""
    if name == "ollama":
        base_url = (req.base_url if req and req.base_url else settings.ollama_base_url)
        model = (req.model if req and req.model else settings.ollama_model)
        provider = OllamaProvider(base_url=base_url, model=model, num_ctx=settings.ollama_num_ctx)
    elif name == "gemini":
        api_key = (req.api_key if req and req.api_key else settings.gemini_api_key)
        model = (req.model if req and req.model else settings.gemini_model)
        if not api_key:
            raise HTTPException(status_code=400, detail="No Gemini API key configured")
        provider = GeminiProvider(api_key=api_key, model=model)
    elif name == "nvidia":
        api_key = (req.api_key if req and req.api_key else settings.nvidia_api_key)
        model = (req.model if req and req.model else settings.nvidia_model)
        if not api_key:
            raise HTTPException(status_code=400, detail="No NVIDIA API key configured")
        provider = NvidiaProvider(api_key=api_key, model=model)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown or unimplemented engine: {name}")

    try:
        status = await provider.test_connection()
    finally:
        await provider.aclose()
    return {"ok": status.ok, "detail": status.detail}


class PullModelRequest(BaseModel):
    base_url: str | None = None  # blank = use currently saved value
    model: str | None = None  # blank = use currently saved value


@router.post("/ollama/pull")
async def pull_ollama_model(req: PullModelRequest):
    if pull_state.current_pull.active:
        return {"started": False, "reason": "A model pull is already in progress"}

    base_url = req.base_url or settings.ollama_base_url
    model = req.model or settings.ollama_model
    provider = OllamaProvider(base_url=base_url, model=model)

    async def _run():
        try:
            await pull_state.run_pull(provider, model)
        finally:
            await provider.aclose()

    asyncio.create_task(_run())
    return {"started": True, "model": model}


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
