import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import state
from app.config import settings
from app.db import settings_store
from app.providers import pull_state
from app.providers.gemini_provider import GeminiProvider
from app.providers.groq_provider import GroqProvider
from app.providers.llamacpp_provider import LlamaCppProvider
from app.providers.nvidia_provider import NvidiaProvider
from app.providers.ollama_provider import OllamaProvider
from app.providers.openrouter_provider import OpenRouterProvider

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
    ollama_batch_token_budget: int = 400
    llamacpp_base_url: str = "http://localhost:8080"
    llamacpp_batch_token_budget: int = 400
    gemini_model: str
    gemini_api_key: str | None = None  # only set to overwrite; omitted = unchanged
    gemini_batch_token_budget: int = 4000
    gemini_concurrent_batch_window: int = 3
    nvidia_model: str = "deepseek-ai/deepseek-v4-flash"
    nvidia_api_key: str | None = None  # only set to overwrite; omitted = unchanged
    nvidia_batch_token_budget: int = 700
    nvidia_concurrent_batch_window: int = 4
    openrouter_model: str = "google/gemma-4-26b-a4b-it:free"
    openrouter_api_key: str | None = None  # only set to overwrite; omitted = unchanged
    openrouter_batch_token_budget: int = 4000
    openrouter_concurrent_batch_window: int = 4
    groq_model: str = "llama-3.1-8b-instant"
    groq_api_key: str | None = None  # only set to overwrite; omitted = unchanged
    groq_batch_token_budget: int = 1800
    groq_concurrent_batch_window: int = 1


@router.get("")
async def get_engine_config():
    return {
        "active_engine": settings.active_engine,
        "fallback_engine": settings.fallback_engine,
        "ollama_base_url": settings.ollama_base_url,
        "ollama_model": settings.ollama_model,
        "ollama_num_ctx": settings.ollama_num_ctx,
        "ollama_batch_token_budget": settings.ollama_batch_token_budget,
        "llamacpp_base_url": settings.llamacpp_base_url,
        "llamacpp_batch_token_budget": settings.llamacpp_batch_token_budget,
        "gemini_model": settings.gemini_model,
        "gemini_api_key_masked": _mask(settings.gemini_api_key),
        "gemini_has_key": bool(settings.gemini_api_key),
        "gemini_batch_token_budget": settings.gemini_batch_token_budget,
        "gemini_concurrent_batch_window": settings.gemini_concurrent_batch_window,
        "nvidia_model": settings.nvidia_model,
        "nvidia_api_key_masked": _mask(settings.nvidia_api_key),
        "nvidia_has_key": bool(settings.nvidia_api_key),
        "nvidia_batch_token_budget": settings.nvidia_batch_token_budget,
        "nvidia_concurrent_batch_window": settings.nvidia_concurrent_batch_window,
        "openrouter_model": settings.openrouter_model,
        "openrouter_api_key_masked": _mask(settings.openrouter_api_key),
        "openrouter_has_key": bool(settings.openrouter_api_key),
        "openrouter_batch_token_budget": settings.openrouter_batch_token_budget,
        "openrouter_concurrent_batch_window": settings.openrouter_concurrent_batch_window,
        "groq_model": settings.groq_model,
        "groq_api_key_masked": _mask(settings.groq_api_key),
        "groq_has_key": bool(settings.groq_api_key),
        "groq_batch_token_budget": settings.groq_batch_token_budget,
        "groq_concurrent_batch_window": settings.groq_concurrent_batch_window,
    }


@router.post("")
async def set_engine_config(config: EngineConfig, conn=Depends(state.get_conn)):
    if config.ollama_num_ctx < 512:
        raise HTTPException(status_code=422, detail="ollama_num_ctx must be at least 512")
    if config.ollama_batch_token_budget < 0:
        raise HTTPException(status_code=422, detail="ollama_batch_token_budget must be >= 0")
    if config.llamacpp_batch_token_budget < 0:
        raise HTTPException(status_code=422, detail="llamacpp_batch_token_budget must be >= 0")
    if config.nvidia_batch_token_budget < 1:
        raise HTTPException(status_code=422, detail="nvidia_batch_token_budget must be at least 1")
    if config.nvidia_concurrent_batch_window < 1:
        raise HTTPException(status_code=422, detail="nvidia_concurrent_batch_window must be at least 1")
    if config.openrouter_batch_token_budget < 1:
        raise HTTPException(
            status_code=422, detail="openrouter_batch_token_budget must be at least 1"
        )
    if config.openrouter_concurrent_batch_window < 1:
        raise HTTPException(
            status_code=422, detail="openrouter_concurrent_batch_window must be at least 1"
        )
    if config.gemini_batch_token_budget < 1:
        raise HTTPException(
            status_code=422, detail="gemini_batch_token_budget must be at least 1"
        )
    if config.gemini_concurrent_batch_window < 1:
        raise HTTPException(
            status_code=422, detail="gemini_concurrent_batch_window must be at least 1"
        )
    if config.groq_batch_token_budget < 1:
        raise HTTPException(status_code=422, detail="groq_batch_token_budget must be at least 1")
    if config.groq_concurrent_batch_window < 1:
        raise HTTPException(
            status_code=422, detail="groq_concurrent_batch_window must be at least 1"
        )
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
    settings.llamacpp_base_url = config.llamacpp_base_url
    settings_store.save_one(conn, "llamacpp_base_url", config.llamacpp_base_url)
    settings.llamacpp_batch_token_budget = config.llamacpp_batch_token_budget
    settings_store.save_one(
        conn, "llamacpp_batch_token_budget", config.llamacpp_batch_token_budget
    )
    settings.gemini_model = config.gemini_model
    settings_store.save_one(conn, "gemini_model", config.gemini_model)
    if config.gemini_api_key:
        settings.gemini_api_key = config.gemini_api_key
        settings_store.save_one(conn, "gemini_api_key", config.gemini_api_key)
    settings.gemini_batch_token_budget = config.gemini_batch_token_budget
    settings_store.save_one(conn, "gemini_batch_token_budget", config.gemini_batch_token_budget)
    settings.gemini_concurrent_batch_window = config.gemini_concurrent_batch_window
    settings_store.save_one(
        conn, "gemini_concurrent_batch_window", config.gemini_concurrent_batch_window
    )
    settings.nvidia_model = config.nvidia_model
    settings_store.save_one(conn, "nvidia_model", config.nvidia_model)
    if config.nvidia_api_key:
        settings.nvidia_api_key = config.nvidia_api_key
        settings_store.save_one(conn, "nvidia_api_key", config.nvidia_api_key)
    settings.nvidia_batch_token_budget = config.nvidia_batch_token_budget
    settings_store.save_one(conn, "nvidia_batch_token_budget", config.nvidia_batch_token_budget)
    settings.nvidia_concurrent_batch_window = config.nvidia_concurrent_batch_window
    settings_store.save_one(
        conn, "nvidia_concurrent_batch_window", config.nvidia_concurrent_batch_window
    )
    settings.openrouter_model = config.openrouter_model
    settings_store.save_one(conn, "openrouter_model", config.openrouter_model)
    if config.openrouter_api_key:
        settings.openrouter_api_key = config.openrouter_api_key
        settings_store.save_one(conn, "openrouter_api_key", config.openrouter_api_key)
    settings.openrouter_batch_token_budget = config.openrouter_batch_token_budget
    settings_store.save_one(
        conn, "openrouter_batch_token_budget", config.openrouter_batch_token_budget
    )
    settings.openrouter_concurrent_batch_window = config.openrouter_concurrent_batch_window
    settings_store.save_one(
        conn, "openrouter_concurrent_batch_window", config.openrouter_concurrent_batch_window
    )
    settings.groq_model = config.groq_model
    settings_store.save_one(conn, "groq_model", config.groq_model)
    if config.groq_api_key:
        settings.groq_api_key = config.groq_api_key
        settings_store.save_one(conn, "groq_api_key", config.groq_api_key)
    settings.groq_batch_token_budget = config.groq_batch_token_budget
    settings_store.save_one(conn, "groq_batch_token_budget", config.groq_batch_token_budget)
    settings.groq_concurrent_batch_window = config.groq_concurrent_batch_window
    settings_store.save_one(
        conn, "groq_concurrent_batch_window", config.groq_concurrent_batch_window
    )
    return {"saved": True}


class TestEngineRequest(BaseModel):
    base_url: str | None = None  # ollama/llamacpp only; blank = use currently saved value
    model: str | None = None  # blank = use currently saved value
    api_key: str | None = None  # cloud providers only; blank = use currently saved value


@router.post("/{name}/test")
async def test_engine(name: str, req: TestEngineRequest | None = None):
    """Tests the given (possibly unsaved) form values directly, without
    persisting them — mirrors /api/config/bazarr/test. Falls back to saved
    settings for any field left blank."""
    if name == "ollama":
        base_url = (req.base_url if req and req.base_url else settings.ollama_base_url)
        model = (req.model if req and req.model else settings.ollama_model)
        provider = OllamaProvider(base_url=base_url, model=model, num_ctx=settings.ollama_num_ctx)
    elif name == "llamacpp":
        base_url = (req.base_url if req and req.base_url else settings.llamacpp_base_url)
        provider = LlamaCppProvider(base_url=base_url)
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
    elif name == "openrouter":
        api_key = (req.api_key if req and req.api_key else settings.openrouter_api_key)
        model = (req.model if req and req.model else settings.openrouter_model)
        if not api_key:
            raise HTTPException(status_code=400, detail="No OpenRouter API key configured")
        provider = OpenRouterProvider(api_key=api_key, model=model)
    elif name == "groq":
        api_key = (req.api_key if req and req.api_key else settings.groq_api_key)
        model = (req.model if req and req.model else settings.groq_model)
        if not api_key:
            raise HTTPException(status_code=400, detail="No Groq API key configured")
        provider = GroqProvider(api_key=api_key, model=model)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown or unimplemented engine: {name}")

    try:
        status = await provider.test_connection()
    finally:
        await provider.aclose()
    return {"ok": status.ok, "detail": status.detail}


@router.get("/ollama/models")
async def list_ollama_models(base_url: str | None = None):
    """Lists models already pulled on the Ollama server — for the Engine
    page's model picker. Accepts an optional (possibly unsaved) base_url
    query param, same pattern as /test, so switching the URL field updates
    the list before the form is saved."""
    resolved_base_url = base_url or settings.ollama_base_url
    provider = OllamaProvider(base_url=resolved_base_url, model="")
    try:
        models = await provider.list_models()
    except Exception as exc:  # noqa: BLE001 - surface to the UI, don't crash the app
        raise HTTPException(status_code=502, detail=f"Could not reach Ollama: {exc}") from exc
    finally:
        await provider.aclose()
    return {"models": models}


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
