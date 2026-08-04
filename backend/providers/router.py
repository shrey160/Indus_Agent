import asyncio

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db
from . import registry

router = APIRouter(prefix="/api/providers", tags=["providers"])


class ProviderCreate(BaseModel):
    name: str
    base_url: str
    type: str = "openai"


class ActivateRequest(BaseModel):
    model: str


class TestRequest(BaseModel):
    model: str
    prompt: str


async def _list_with_status(statuses: dict) -> list[dict]:
    rows = await db.fetch(
        "SELECT id, name, base_url, type, is_default FROM providers ORDER BY id"
    )
    out = []
    for row in rows:
        status = statuses.get(row["id"])
        out.append(
            {
                **dict(row),
                "status": status.to_dict() if status else {"state": "checking", "latency_ms": None, "error": None, "models": []},
            }
        )
    return out


async def _get_provider_or_404(provider_id: int):
    row = await db.fetchrow(
        "SELECT id, name, base_url, type, is_default FROM providers WHERE id = $1",
        provider_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="provider not found")
    return row


@router.get("")
async def list_providers() -> list[dict]:
    statuses = await registry.detect_all()
    return await _list_with_status(statuses)


@router.post("/detect")
async def detect_providers() -> list[dict]:
    statuses = await registry.detect_all(force=True)
    return await _list_with_status(statuses)


@router.post("", status_code=201)
async def create_provider(body: ProviderCreate) -> dict:
    if body.type not in ("ollama", "openai"):
        raise HTTPException(status_code=400, detail="type must be 'ollama' or 'openai'")
    base_url = registry.normalize_base_url(body.base_url)
    probe_row = {"name": body.name, "base_url": base_url, "type": body.type}
    status = await registry.build_provider(probe_row).ping()
    if status.state == "down":
        raise HTTPException(
            status_code=400, detail=f"provider unreachable: {status.error}"
        )
    row = await db.fetchrow(
        """
        INSERT INTO providers (name, base_url, type)
        VALUES ($1, $2, $3)
        RETURNING id, name, base_url, type, is_default
        """,
        body.name,
        base_url,
        body.type,
    )
    registry.invalidate_cache()
    return {**dict(row), "status": status.to_dict()}


@router.get("/active")
async def active_provider() -> dict:
    row = await db.fetchrow(
        """
        SELECT s.active_provider_id AS provider_id, p.name AS provider_name, s.active_model AS model
        FROM app_state s
        LEFT JOIN providers p ON p.id = s.active_provider_id
        WHERE s.id = TRUE
        """
    )
    if row is None:
        return {"provider_id": None, "provider_name": None, "model": None}
    return dict(row)


@router.delete("/{provider_id}")
async def delete_provider(provider_id: int) -> dict:
    row = await _get_provider_or_404(provider_id)
    if row["is_default"]:
        raise HTTPException(status_code=409, detail="cannot delete a default provider")
    await db.execute("DELETE FROM providers WHERE id = $1", provider_id)
    registry.invalidate_cache()
    return {"ok": True}


@router.get("/{provider_id}/models")
async def provider_models(provider_id: int) -> list[dict]:
    row = await _get_provider_or_404(provider_id)
    try:
        models = await registry.build_provider(row).models()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc) or type(exc).__name__)
    return [{"id": m.id, "size_bytes": m.size_bytes} for m in models]


@router.post("/{provider_id}/test")
async def test_provider(provider_id: int, body: TestRequest) -> dict:
    row = await _get_provider_or_404(provider_id)
    return await registry.build_provider(row).test(body.model, body.prompt)


@router.post("/{provider_id}/activate")
async def activate_provider(provider_id: int, body: ActivateRequest) -> dict:
    row = await _get_provider_or_404(provider_id)
    await registry.activate(provider_id, body.model)
    provider = registry.build_provider(row)
    try:
        if await provider.is_loaded(body.model):
            return {"ok": True, "skipped": True}
    except Exception:
        pass
    try:
        warmup_ms = await provider.warmup(body.model)
        return {"ok": True, "warmup_ms": warmup_ms}
    except Exception as exc:
        if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError)):
            return {"ok": True, "warmup_pending": True}
        return {"ok": True, "warmup_error": str(exc) or type(exc).__name__}
