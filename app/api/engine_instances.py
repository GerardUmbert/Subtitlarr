from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import state
from app.db import engine_instances_repo
from app.providers import registry

router = APIRouter(prefix="/api/config/engine-instances", tags=["engine-instances"])

_SECRET_FIELDS_BY_TYPE = {
    "gemini": ["api_key"],
    "nvidia": ["api_key"],
    "openrouter": ["api_key"],
    "groq": ["api_key"],
    "llamacpp": ["api_key"],  # optional — llama.cpp itself has no built-in auth
    "ollama": [],
}


def _mask(secret: str) -> str:
    if not secret:
        return ""
    if len(secret) <= 6:
        return "***"
    return f"{secret[:3]}...{secret[-2:]}"


def _public_instance(instance: dict) -> dict:
    """Never send a real secret back to the client — mask any configured
    key, plus a has_key flag so the UI can show 'configured' without the
    value itself. Same masking convention the old single-engine API used."""
    config = dict(instance["config"])
    public = {**instance, "config": config}
    for field in _SECRET_FIELDS_BY_TYPE.get(instance["provider_type"], []):
        secret = config.get(field, "")
        config[f"{field}_masked"] = _mask(secret)
        config[f"has_{field}"] = bool(secret)
        config[field] = None  # never echo the real value back
    return public


@router.get("")
def list_engine_instances(conn=Depends(state.get_conn)):
    instances = engine_instances_repo.list_instances(conn)
    return {"data": [_public_instance(i) for i in instances]}


class CreateInstanceRequest(BaseModel):
    name: str
    provider_type: str
    config: dict = {}
    enabled: bool = True


def _validate_provider_type(provider_type: str) -> None:
    if provider_type == engine_instances_repo.SEPARATOR_TYPE:
        return
    if provider_type not in registry.DEFAULT_CONFIG_BY_TYPE:
        raise HTTPException(
            status_code=422, detail=f"Unknown provider_type: {provider_type!r}"
        )


@router.post("")
def create_engine_instance(req: CreateInstanceRequest, conn=Depends(state.get_conn)):
    _validate_provider_type(req.provider_type)
    if req.provider_type == engine_instances_repo.SEPARATOR_TYPE:
        config = {}
    else:
        # Fill in any field the caller didn't supply with that provider
        # type's default — a freshly-created instance should work out of
        # the box (matching whatever model/URL defaults the old single-
        # engine Settings fields used to ship with) rather than needing
        # every field specified up front.
        config = {**registry.DEFAULT_CONFIG_BY_TYPE[req.provider_type], **req.config}
        try:
            registry.validate_temperature(config.get("temperature"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    instance = engine_instances_repo.create_instance(
        conn, name=req.name, provider_type=req.provider_type, config=config, enabled=req.enabled
    )
    return _public_instance(instance)


class UpdateInstanceRequest(BaseModel):
    name: str | None = None
    config: dict | None = None
    enabled: bool | None = None


@router.put("/{instance_id}")
def update_engine_instance(
    instance_id: int, req: UpdateInstanceRequest, conn=Depends(state.get_conn)
):
    existing = engine_instances_repo.get_instance(conn, instance_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Engine instance not found")

    merged_config = None
    if req.config is not None:
        # Merge onto the EXISTING config rather than replacing it wholesale
        # — the request body only carries the fields the form actually
        # showed the user (a masked-out secret field submitted as None/
        # blank means "leave unchanged", never "clear it"), matching the
        # old single-engine API's save behavior for API keys.
        merged_config = dict(existing["config"])
        for key, value in req.config.items():
            if value is None or value == "":
                continue
            merged_config[key] = value
        try:
            registry.validate_temperature(merged_config.get("temperature"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    updated = engine_instances_repo.update_instance(
        conn, instance_id, name=req.name, config=merged_config, enabled=req.enabled
    )
    return _public_instance(updated)


@router.delete("/{instance_id}")
def delete_engine_instance(instance_id: int, conn=Depends(state.get_conn)):
    existing = engine_instances_repo.get_instance(conn, instance_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Engine instance not found")
    engine_instances_repo.delete_instance(conn, instance_id)
    return {"deleted": True}


class ReorderRequest(BaseModel):
    ids: list[int]


@router.post("/reorder")
def reorder_engine_instances(req: ReorderRequest, conn=Depends(state.get_conn)):
    engine_instances_repo.reorder_instances(conn, req.ids)
    return {"data": [_public_instance(i) for i in engine_instances_repo.list_instances(conn)]}


class TestInstanceRequest(BaseModel):
    """Tests the given (possibly unsaved) form values directly, without
    persisting them — mirrors the old /api/config/engines/{name}/test.
    Any field left out falls back to the instance's currently saved
    config, same pattern as before."""
    config: dict = {}


@router.post("/{instance_id}/test")
async def test_engine_instance(
    instance_id: int, req: TestInstanceRequest | None = None, conn=Depends(state.get_conn)
):
    instance = engine_instances_repo.get_instance(conn, instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail="Engine instance not found")
    if instance["provider_type"] == engine_instances_repo.SEPARATOR_TYPE:
        raise HTTPException(status_code=400, detail="A separator has no connection to test")

    config = dict(instance["config"])
    if req is not None:
        for key, value in req.config.items():
            if value:
                config[key] = value

    secret_fields = _SECRET_FIELDS_BY_TYPE.get(instance["provider_type"], [])
    for field in secret_fields:
        if field != "api_key":
            continue
        if not config.get("api_key") and instance["provider_type"] not in ("ollama", "llamacpp"):
            raise HTTPException(
                status_code=400,
                detail=f"No API key configured for {instance['provider_type']}",
            )

    provider = registry.build_provider(instance["provider_type"], config)
    try:
        status = await provider.test_connection()
    finally:
        if hasattr(provider, "aclose"):
            await provider.aclose()

    if status.ok:
        # A successful manual test is strong evidence the underlying issue
        # (bad key, unreachable local server) is resolved — clear an early
        # cooldown instead of making the user wait out the full 24h.
        engine_instances_repo.clear_rate_limit(conn, instance_id)

    return {"ok": status.ok, "detail": status.detail}
