import logging
import os

import db
from memory.budget import RAG_BUDGET, fit
from rag.chunker import MIN_ALNUM
from rag.embedder import EMBED_MODEL, embed_batch

logger = logging.getLogger(__name__)

RAG_AUTO_MIN_SCORE = float(os.environ.get("RAG_AUTO_MIN_SCORE", "0.65"))


async def search_chunks(query: str, top_k: int = 3, min_score: float = 0.5) -> list[dict]:
    """Semantic search over ready document chunks.

    Returns a list of dicts with keys: chunk_id, idx, page, doc, content, score.
    """
    try:
        vectors = await embed_batch([query])
    except RuntimeError as exc:
        logger.warning("rag context embedding failed: %s", exc)
        return []

    if not vectors or not vectors[0]:
        return []

    query_vec = vectors[0]
    rows = await db.fetch(
        """
        SELECT
            c.id AS chunk_id,
            c.idx,
            c.page,
            d.filename AS doc,
            c.content,
            1 - (c.embedding <=> $1::vector) AS score
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE c.embed_model = $2
          AND d.status = 'ready'
        ORDER BY c.embedding <=> $1::vector
        LIMIT $3
        """,
        query_vec,
        EMBED_MODEL,
        top_k,
    )

    results = []
    for row in rows:
        if sum(ch.isalnum() for ch in row["content"]) < MIN_ALNUM:
            continue
        score = float(row["score"]) if row["score"] is not None else 0.0
        if score < min_score:
            continue
        results.append(
            {
                "chunk_id": row["chunk_id"],
                "idx": row["idx"],
                "page": row["page"],
                "doc": row["doc"],
                "content": row["content"],
                "score": score,
            }
        )
    return results


async def rag_context(user_message: str) -> tuple[str, list[dict]]:
    """Build a <context> block for the user message and return source metadata.

    Returns (context_block, sources).  The block is trimmed to RAG_BUDGET.
    """
    chunks = await search_chunks(user_message, top_k=3, min_score=RAG_AUTO_MIN_SCORE)
    if not chunks:
        return "", []

    lines: list[str] = []
    sources: list[dict] = []
    for i, chunk in enumerate(chunks, start=1):
        lines.append(f"[{i}] ({chunk['doc']}, chunk {chunk['idx']})")
        lines.append(chunk["content"])
        sources.append(
            {
                "kind": "rag",
                "doc": chunk["doc"],
                "chunk_id": chunk["chunk_id"],
                "snippet": chunk["content"][:400],
                "score": chunk["score"],
            }
        )

    raw_block = "\n\n".join(lines)
    fitted = fit(raw_block, RAG_BUDGET)
    block = f"<context>\n{fitted}\n</context>"
    return block, sources
