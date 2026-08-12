import asyncio
import re
import time

import asyncpg

import db
from . import crypto
from .base import ProviderBase, ProviderStatus, compute_cost, fmt_err
from .cloud import CloudProvider, CLOUD_PING_TIMEOUT
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatProvider

CACHE_TTL = 30.0
PROBE_TIMEOUT = 1.5

_cache_ts = 0.0
_cache: dict[int, ProviderStatus] = {}
PRICING: dict[tuple[int, str], dict] = {}


def cost_for(provider_id: int, model: str, usage) -> float | None:
    entry = PRICING.get((provider_id, model))
    if not entry or not usage:
        return None
    return compute_cost(entry["pricing"], usage)


def context_length(provider_id: int, model: str) -> int | None:
    entry = PRICING.get((provider_id, model))
    return entry["context_length"] if entry else None


def _decrypt_key(row: dict) -> str:
    api_key = ""
    api_key_enc = row.get("api_key_enc")
    if api_key_enc:
        try:
            api_key = crypto.decrypt(api_key_enc)
        except Exception:
            api_key = ""
    return api_key


def build_provider(row: asyncpg.Record | dict) -> ProviderBase:
    row = dict(row)
    if row["type"] == "ollama":
        return OllamaProvider(row["name"], row["base_url"])
    if row.get("kind") == "cloud":
        return CloudProvider(
            row["name"],
            row["base_url"],
            _decrypt_key(row),
            preset=row.get("preset"),
        )
    return OpenAICompatProvider(row["name"], row["base_url"], _decrypt_key(row))


def _rewrite_loopback(url: str) -> str:
    """127.0.0.1 / localhost resolve inside the container to the container itself;
    the host's loopback is only reachable via host.docker.internal."""
    url = re.sub(
        r"^([a-z]+://)127\.0\.0\.1(?=[:/])", r"\1host.docker.internal", url, flags=re.I
    )
    url = re.sub(
        r"^([a-z]+://)localhost(?=[:/])", r"\1host.docker.internal", url, flags=re.I
    )
    return url


def normalize_base_url(raw: str) -> str:
    url = (raw or "").strip()
    port_shorthand = re.fullmatch(r"\d+", url)
    # base_url must not carry a /v1 suffix (HP-006) — subpaths append it.
    if url.endswith("/v1"):
        url = url[: -len("/v1")]
    if port_shorthand:
        url = f"http://host.docker.internal:{url}"
    elif not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    url = _rewrite_loopback(url)
    return url.rstrip("/")


def invalidate_cache() -> None:
    global _cache_ts
    _cache_ts = 0.0


async def detect_all(force: bool = False) -> dict[int, ProviderStatus]:
    global _cache_ts, _cache
    now = time.monotonic()
    if not force and now - _cache_ts < CACHE_TTL:
        return _cache
    rows = await db.fetch(
        "SELECT id, name, base_url, type, kind, preset, api_key_enc FROM providers ORDER BY id"
    )

    async def probe(row) -> tuple[int, ProviderStatus]:
        provider = build_provider(row)
        timeout = CLOUD_PING_TIMEOUT if dict(row).get("kind") == "cloud" else PROBE_TIMEOUT
        try:
            status = await asyncio.wait_for(provider.ping(), timeout=timeout + 1.0)
        except Exception as exc:
            state = "unreachable" if dict(row).get("kind") == "cloud" else "down"
            status = ProviderStatus(state=state, error=fmt_err(exc))
        return row["id"], status

    results = await asyncio.gather(*(probe(row) for row in rows))
    _cache = dict(results)
    _cache_ts = now
    for pid, status in _cache.items():
        for m in status.models:
            PRICING[(pid, m.id)] = {
                "pricing": m.pricing,
                "context_length": m.context_length,
                "is_free": m.is_free,
            }
    return _cache


async def activate(provider_id: int, model: str) -> None:
    await db.execute(
        "UPDATE app_state SET active_provider_id = $1, active_model = $2 WHERE id = TRUE",
        provider_id,
        model,
    )


async def favorites(provider_id: int) -> set[str]:
    rows = await db.fetch(
        "SELECT model_id FROM provider_favorites WHERE provider_id = $1",
        provider_id,
    )
    return {r["model_id"] for r in rows}


async def toggle_favorite(provider_id: int, model_id: str) -> dict:
    exists = await db.fetchval(
        "SELECT 1 FROM provider_favorites WHERE provider_id = $1 AND model_id = $2",
        provider_id,
        model_id,
    )
    if exists:
        await db.execute(
            "DELETE FROM provider_favorites WHERE provider_id = $1 AND model_id = $2",
            provider_id,
            model_id,
        )
        return {"pinned": False}
    await db.execute(
        "INSERT INTO provider_favorites (provider_id, model_id) VALUES ($1, $2)",
        provider_id,
        model_id,
    )
    return {"pinned": True}
