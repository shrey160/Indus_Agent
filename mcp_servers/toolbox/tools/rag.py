import os
from typing import Any

import asyncpg
import httpx

DATABASE_URL = os.environ["DATABASE_URL"]
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")
EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768
BATCH_SIZE = 16

_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=4)
    return _pool


async def _embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    base = OLLAMA_URL.rstrip("/")
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(
                f"{base}/api/embed",
                json={"model": EMBED_MODEL, "input": texts},
            )
            if response.status_code == 200:
                data = response.json()
                embeddings = data.get("embeddings") or []
                if len(embeddings) != len(texts):
                    raise RuntimeError("embedding batch size mismatch")
                for vec in embeddings:
                    if len(vec) != EMBED_DIM:
                        raise RuntimeError(
                            f"embed dim mismatch: got {len(vec)}, expected {EMBED_DIM}"
                        )
                return embeddings
        except Exception:
            pass

        # Fallback: per-text legacy endpoint.
        embeddings: list[list[float]] = []
        for text in texts:
            last_err: Exception | None = None
            for attempt in range(3):
                try:
                    response = await client.post(
                        f"{base}/api/embeddings",
                        json={"model": EMBED_MODEL, "prompt": text},
                    )
                    response.raise_for_status()
                    vec = response.json().get("embedding") or []
                    if len(vec) != EMBED_DIM:
                        raise RuntimeError(
                            f"embed dim mismatch: got {len(vec)}, expected {EMBED_DIM}"
                        )
                    embeddings.append(vec)
                    break
                except Exception as exc:
                    last_err = exc
                    await __import__("asyncio").sleep(2**attempt)
            else:
                raise RuntimeError(
                    "is Ollama running / nomic-embed-text pulled?"
                ) from last_err
        return embeddings


def _vec_literal(v: list[float]) -> str:
    return "[" + ",".join(str(float(x)) for x in v) + "]"


def _chunk_text(text: str, target: int = 2000, overlap: int = 200) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    def finalize() -> str:
        return "\n\n".join(current_parts)

    for para in paragraphs:
        para_len = len(para)
        # If a single paragraph exceeds target, put it in its own chunk.
        if current_parts and current_len + para_len + 2 > target:
            chunks.append(finalize())
            prefix = ""
            if chunks and overlap > 0:
                prev = chunks[-1]
                raw = prev[-overlap:] if len(prev) > overlap else prev
                snap = raw.lstrip()
                for i, ch in enumerate(raw):
                    if ch.isspace():
                        snap = raw[i + 1 :].lstrip()
                        break
                prefix = snap
            current_parts = [prefix] if prefix else []
            current_len = sum(len(p) for p in current_parts)
        current_parts.append(para)
        current_len += para_len + (2 if len(current_parts) > 1 else 0)

    if current_parts:
        chunks.append(finalize())

    # Drop any whitespace-only chunks that can arise from overlap prefixes.
    return [c.strip() for c in chunks if c.strip()]


async def rag_search(query: str, top_k: int = 5, min_score: float = 0.5) -> dict[str, Any]:
    """Semantic search over the user's uploaded documents.
    Returns {query, results: [{doc, chunk_id, snippet, score}], source: 'rag'}.
    """
    try:
        embeddings = await _embed([query])
        query_vec = embeddings[0]
    except Exception as exc:
        raise RuntimeError(f"embedding failed: {exc}") from exc

    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id AS chunk_id, c.idx, d.filename AS doc,
                   left(c.content, 400) AS snippet,
                   1 - (c.embedding <=> $1::vector) AS score
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.embed_model = $2 AND d.status = 'ready'
              AND 1 - (c.embedding <=> $1::vector) >= $4
            ORDER BY c.embedding <=> $1::vector
            LIMIT $3
            """,
            _vec_literal(query_vec),
            EMBED_MODEL,
            top_k,
            min_score,
        )

    results = [
        {
            "doc": r["doc"],
            "chunk_id": r["chunk_id"],
            "idx": r["idx"],
            "snippet": r["snippet"],
            "score": float(r["score"]),
        }
        for r in rows
    ]
    return {"query": query, "results": results, "source": "rag"}


async def rag_ingest_text(text: str, title: str) -> dict[str, Any]:
    """Index an ad-hoc text note into the document store for later rag.search."""
    chunks = _chunk_text(text)
    if not chunks:
        return {"ok": False, "error": "no indexable text", "source": "rag"}

    pool = await _get_pool()
    async with pool.acquire() as conn:
        doc_id = await conn.fetchval(
            "INSERT INTO documents (filename, path, status) VALUES ($1, '', 'processing') RETURNING id",
            title,
        )

        try:
            for i in range(0, len(chunks), BATCH_SIZE):
                batch = chunks[i : i + BATCH_SIZE]
                embeddings = await _embed(batch)
                await conn.executemany(
                    """
                    INSERT INTO chunks (document_id, idx, content, page, embedding, embed_model)
                    VALUES ($1, $2, $3, NULL, $4::vector, $5)
                    """,
                    [
                        (
                            doc_id,
                            idx,
                            content,
                            _vec_literal(vec),
                            EMBED_MODEL,
                        )
                        for idx, (content, vec) in enumerate(
                            zip(batch, embeddings), start=i
                        )
                    ],
                )

            await conn.execute(
                "UPDATE documents SET status = 'ready', chunk_count = $2 WHERE id = $1",
                doc_id,
                len(chunks),
            )
        except Exception as exc:
            error_msg = str(exc)[:500]
            await conn.execute(
                "UPDATE documents SET status = 'failed', error = $2 WHERE id = $1",
                doc_id,
                error_msg,
            )
            return {"ok": False, "error": error_msg, "source": "rag"}

    return {
        "ok": True,
        "document_id": doc_id,
        "chunk_count": len(chunks),
        "source": "rag",
    }
