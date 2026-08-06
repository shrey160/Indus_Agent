import asyncio
import logging
from pathlib import Path

import db
from .chunker import chunk_document
from .embedder import BATCH_SIZE, EMBED_MODEL, embed_batch
from .loaders import load_document

logger = logging.getLogger(__name__)


async def run_ingest(doc_id: int) -> None:
    row = await db.fetchrow("SELECT * FROM documents WHERE id = $1", doc_id)
    if row is None:
        logger.warning("run_ingest called for missing document %s", doc_id)
        return

    await db.execute(
        "UPDATE documents SET status = 'processing', error = NULL WHERE id = $1",
        doc_id,
    )

    try:
        pages = load_document(Path(row["path"]))
        chunks = chunk_document(pages)

        # Idempotent re-ingest: clear previous chunks.
        await db.execute("DELETE FROM chunks WHERE document_id = $1", doc_id)

        for i in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[i : i + BATCH_SIZE]
            vectors = await embed_batch([c["content"] for c in batch])
            for c, vec in zip(batch, vectors):
                await db.execute(
                    """
                    INSERT INTO chunks (document_id, idx, content, page, embedding, embed_model)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    doc_id,
                    c["idx"],
                    c["content"],
                    c["page"],
                    vec,
                    EMBED_MODEL,
                )

        await db.execute(
            "UPDATE documents SET status = 'ready', chunk_count = $2 WHERE id = $1",
            doc_id,
            len(chunks),
        )
    except Exception as exc:
        msg = str(exc)[:500]
        logger.exception("ingest failed for document %s: %s", doc_id, msg)
        await db.execute(
            "UPDATE documents SET status = 'failed', error = $2 WHERE id = $1",
            doc_id,
            msg,
        )


def _done_callback(task: asyncio.Task) -> None:
    exc = task.exception()
    if exc:
        logger.exception("background ingest task failed: %s", exc)


def kickoff_ingest(doc_id: int) -> None:
    task = asyncio.create_task(run_ingest(doc_id))
    task.add_done_callback(_done_callback)
