import asyncio
import logging

import httpx

import db

logger = logging.getLogger(__name__)

EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768
BATCH_SIZE = 16
EMBED_TIMEOUT = 60.0


async def _ollama_base() -> str:
    rows = await db.fetch(
        "SELECT base_url FROM providers WHERE type = 'ollama' ORDER BY id"
    )
    if not rows:
        raise RuntimeError("is Ollama running / nomic-embed-text pulled?")
    return rows[0]["base_url"].rstrip("/")


def _assert_dim(vectors: list[list[float]]) -> None:
    for vec in vectors:
        if len(vec) != EMBED_DIM:
            raise RuntimeError(f"embed dim mismatch: got {len(vec)}, expected {EMBED_DIM}")


async def embed_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    base = await _ollama_base()
    last_exc: Exception | None = None

    for attempt, sleep_sec in enumerate([1, 2, 4]):
        try:
            async with httpx.AsyncClient(timeout=EMBED_TIMEOUT) as client:
                # Try the newer batched endpoint first.
                try:
                    response = await client.post(
                        f"{base}/api/embed",
                        json={"model": EMBED_MODEL, "input": texts},
                    )
                    if response.status_code == 404:
                        raise RuntimeError("batch endpoint not found")
                    response.raise_for_status()
                    payload = response.json()
                    vectors = payload.get("embeddings")
                    if vectors and len(vectors) == len(texts):
                        _assert_dim(vectors)
                        return vectors
                except (httpx.HTTPStatusError, RuntimeError) as exc:
                    logger.debug("batch embed attempt failed (%s), falling back", exc)

                # Fallback: one request per text via the legacy endpoint.
                vectors = []
                for text in texts:
                    response = await client.post(
                        f"{base}/api/embeddings",
                        json={"model": EMBED_MODEL, "prompt": text},
                    )
                    response.raise_for_status()
                    vec = response.json().get("embedding")
                    if not vec:
                        raise RuntimeError("empty embedding in legacy response")
                    vectors.append(list(vec))
                _assert_dim(vectors)
                return vectors
        except Exception as exc:
            last_exc = exc
            logger.warning("embed attempt %d failed: %s", attempt + 1, exc)
            if sleep_sec:
                await asyncio.sleep(sleep_sec)

    raise RuntimeError("is Ollama running / nomic-embed-text pulled?")
