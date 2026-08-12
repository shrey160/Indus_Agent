import asyncio

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db
from . import crypto, registry
from .base import ProviderStatus
from .cloud import CloudProvider
from .openai_compat import OpenAICompatProvider
from .presets import CLOUD_PRESETS

router = APIRouter(prefix="/api/providers", tags=["providers"])

LOCAL_COLS = ("id", "name", "base_url", "type", "is_default", "kind", "preset", "key_hint")


class ProviderCreate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    type: str = "openai"
    kind: str = "local"
    preset: str | None = None
    api_key: str | None = None


class ActivateRequest(BaseModel):
    model: str


class TestRequest(BaseModel):
    model: str
    prompt: str


class KeyRequest(BaseModel):
    api_key: str


class FavoriteRequest(BaseModel):
    model_id: str
    model_config = {"protected_namespaces": ()}


async def _serialize(row, status: ProviderStatus | None) -> dict:
    out = {
        **{col: row[col] for col in LOCAL_COLS},
        "status": status.to_dict() if status else {"state": "checking", "latency_ms": None, "error": None, "models": [], "balance": None},
    }
    if status and status.models:
        try:
            pinned = await registry.favorites(row["id"])
        except Exception:
            pinned = set()
        for m in out["status"]["models"]:
            m["pinned"] = m["id"] in pinned
    return out


async def _list_with_status(statuses: dict) -> list[dict]:
    rows = await db.fetch(
        "SELECT * FROM providers ORDER BY id"
    )
    return [await _serialize(row, statuses.get(row["id"])) for row in rows]


async def _get_provider_or_404(provider_id: int):
    row = await db.fetchrow(
        "SELECT * FROM providers WHERE id = $1",
        provider_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="provider not found")
    return row


async def _provider_status(row, status: ProviderStatus) -> dict:
    return await _serialize(row, status)


async def _reject_cloud_validation(status: ProviderStatus) -> None:
    if status.state == "bad_key":
        raise HTTPException(status_code=400, detail="invalid API key: " + (status.error or "unauthorized"))
    if status.state == "no_credits":
        raise HTTPException(status_code=400, detail="no credits: " + (status.error or "payment required"))


def _resolve_cloud_base(preset: str | None, base_url: str | None) -> str:
    preset_cfg = CLOUD_PRESETS.get(preset or "")
    resolved = (base_url or (preset_cfg["base_url"] if preset_cfg else "") or "").strip().rstrip("/")
    if not resolved:
        raise HTTPException(status_code=400, detail="a base_url or preset is required")
    if not resolved.startswith("https://"):
        raise HTTPException(status_code=400, detail="cloud base URL must use https://")
    return resolved


@router.get("")
async def list_providers() -> list[dict]:
    statuses = await registry.detect_all()
    return await _list_with_status(statuses)


@router.post("/detect")
async def detect_providers() -> list[dict]:
    statuses = await registry.detect_all(force=True)
    return await _list_with_status(statuses)


@router.get("/presets")
async def get_presets() -> dict:
    return CLOUD_PRESETS


@router.post("", status_code=201)
async def create_provider(body: ProviderCreate) -> dict:
    if body.kind == "cloud":
        if not body.api_key:
            raise HTTPException(status_code=400, detail="api_key is required for cloud providers")
        base_url = _resolve_cloud_base(body.preset, body.base_url)
        provider_name = body.name or (CLOUD_PRESETS.get(body.preset or "", {}).get("name") or "Cloud")
        probe = CloudProvider(provider_name, base_url, body.api_key, preset=body.preset)
        status = await probe.ping()
        await _reject_cloud_validation(status)
        row = await db.fetchrow(
            """
            INSERT INTO providers (name, base_url, type, kind, preset, api_key_enc, key_hint)
            VALUES ($1, $2, 'openai', 'cloud', $3, $4, $5)
            RETURNING id, name, base_url, type, is_default, kind, preset, key_hint
            """,
            provider_name,
            base_url,
            body.preset,
            crypto.encrypt(body.api_key),
            crypto.mask(body.api_key),
        )
        registry.invalidate_cache()
        return await _provider_status(row, status)

    if body.type not in ("ollama", "openai"):
        raise HTTPException(status_code=400, detail="type must be 'ollama' or 'openai'")
    if not body.base_url:
        raise HTTPException(status_code=400, detail="base_url is required for local providers")
    if not body.name:
        raise HTTPException(status_code=400, detail="name is required for local providers")
    base_url = registry.normalize_base_url(body.base_url)
    api_key = (body.api_key or "").strip()
    if body.type == "openai":
        probe = OpenAICompatProvider(body.name, base_url, api_key=api_key)
    else:
        if api_key:
            raise HTTPException(status_code=400, detail="API keys are not supported for Ollama providers")
        probe = registry.build_provider(
            {"name": body.name, "base_url": base_url, "type": body.type}
        )
    status = await probe.ping()
    if status.state == "bad_key":
        raise HTTPException(status_code=400, detail="invalid API key")
    if status.state == "down":
        raise HTTPException(
            status_code=400, detail=f"provider unreachable: {status.error}"
        )
    if api_key:
        row = await db.fetchrow(
            """
            INSERT INTO providers (name, base_url, type, kind, api_key_enc, key_hint)
            VALUES ($1, $2, $3, 'local', $4, $5)
            RETURNING id, name, base_url, type, is_default, kind, preset, key_hint
            """,
            body.name,
            base_url,
            body.type,
            crypto.encrypt(api_key),
            crypto.mask(api_key),
        )
    else:
        row = await db.fetchrow(
            """
            INSERT INTO providers (name, base_url, type, kind)
            VALUES ($1, $2, $3, 'local')
            RETURNING id, name, base_url, type, is_default, kind, preset, key_hint
            """,
            body.name,
            base_url,
            body.type,
        )
    registry.invalidate_cache()
    return await _provider_status(row, status)


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


@router.post("/{provider_id}/revalidate")
async def revalidate_provider(provider_id: int) -> dict:
    row = await _get_provider_or_404(provider_id)
    registry.invalidate_cache()
    statuses = await registry.detect_all(force=True)
    return await _provider_status(row, statuses.get(provider_id))


@router.put("/{provider_id}/key")
async def update_key(provider_id: int, body: KeyRequest) -> dict:
    row = await _get_provider_or_404(provider_id)
    can_store_key = row["kind"] == "cloud" or (
        row["kind"] == "local" and row["type"] == "openai"
    )
    if not can_store_key:
        raise HTTPException(status_code=400, detail="providers of this type do not store API keys")
    if not body.api_key:
        raise HTTPException(status_code=400, detail="api_key is required")
    api_key = body.api_key.strip()
    if row["kind"] == "cloud":
        probe = CloudProvider(row["name"], row["base_url"], api_key, preset=row["preset"])
        status = await probe.ping()
        await _reject_cloud_validation(status)
    else:
        probe = OpenAICompatProvider(row["name"], row["base_url"], api_key=api_key)
        status = await probe.ping()
        if status.state == "bad_key":
            raise HTTPException(status_code=400, detail="invalid API key")
        if status.state == "down":
            raise HTTPException(
                status_code=400, detail=f"provider unreachable: {status.error}"
            )
    await db.execute(
        "UPDATE providers SET api_key_enc = $1, key_hint = $2 WHERE id = $3",
        crypto.encrypt(api_key),
        crypto.mask(api_key),
        provider_id,
    )
    registry.invalidate_cache()
    updated = await _get_provider_or_404(provider_id)
    return await _provider_status(updated, status)


@router.get("/{provider_id}/models")
async def provider_models(provider_id: int) -> list[dict]:
    row = await _get_provider_or_404(provider_id)
    try:
        models = await registry.build_provider(row).models()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc) or type(exc).__name__)
    pinned = await registry.favorites(provider_id)
    return [
        {
            "id": m.id,
            "size_bytes": m.size_bytes,
            "pricing": m.pricing,
            "context_length": m.context_length,
            "is_free": m.is_free,
            "pinned": m.id in pinned,
        }
        for m in models
    ]


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


@router.post("/{provider_id}/favorite")
async def toggle_favorite(provider_id: int, body: FavoriteRequest) -> dict:
    await _get_provider_or_404(provider_id)
    return await registry.toggle_favorite(provider_id, body.model_id)
