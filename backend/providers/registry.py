import asyncio
import re
import time

import asyncpg

import db
from .base import ProviderBase, ProviderStatus, fmt_err
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatProvider

CACHE_TTL = 30.0
PROBE_TIMEOUT = 1.5

_cache_ts = 0.0
_cache: dict[int, ProviderStatus] = {}


def build_provider(row: asyncpg.Record | dict) -> ProviderBase:
    if row["type"] == "ollama":
        return OllamaProvider(row["name"], row["base_url"])
    return OpenAICompatProvider(row["name"], row["base_url"])


def normalize_base_url(raw: str) -> str:
    url = raw.strip()
    if re.fullmatch(r"\d+", url):
        url = f"http://host.docker.internal:{url}"
    elif not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url.rstrip("/")


def invalidate_cache() -> None:
    global _cache_ts
    _cache_ts = 0.0


async def detect_all(force: bool = False) -> dict[int, ProviderStatus]:
    global _cache_ts, _cache
    now = time.monotonic()
    if not force and now - _cache_ts < CACHE_TTL:
        return _cache
    rows = await db.fetch("SELECT id, name, base_url, type FROM providers ORDER BY id")

    async def probe(row) -> tuple[int, ProviderStatus]:
        provider = build_provider(row)
        try:
            status = await asyncio.wait_for(provider.ping(), timeout=PROBE_TIMEOUT)
        except Exception as exc:
            status = ProviderStatus(state="down", error=fmt_err(exc))
        return row["id"], status

    results = await asyncio.gather(*(probe(row) for row in rows))
    _cache = dict(results)
    _cache_ts = now
    return _cache


async def activate(provider_id: int, model: str) -> None:
    await db.execute(
        "UPDATE app_state SET active_provider_id = $1, active_model = $2 WHERE id = TRUE",
        provider_id,
        model,
    )
