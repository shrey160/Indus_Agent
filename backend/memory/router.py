import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db
from . import extractor, retriever, soul

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["memory"])


class SoulPut(BaseModel):
    content: str


class ReextractRequest(BaseModel):
    conversation_id: int


class MemoryPut(BaseModel):
    fact: str


@router.get("/soul")
async def get_soul() -> dict:
    content = soul.get_soul()
    return {"content": content, "mtime": soul.get_mtime()}


@router.put("/soul")
async def put_soul(body: SoulPut) -> dict:
    soul.set_soul(body.content)
    return {"content": body.content}


@router.get("/memories")
async def list_memories(q: str | None = None, category: str | None = None) -> list[dict]:
    clauses: list[str] = []
    params: list = []
    if category:
        clauses.append("category = $" + str(len(params) + 1))
        params.append(category)
    if q:
        clauses.append("fact ILIKE '%' || $" + str(len(params) + 1) + " || '%'")
        params.append(q)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = await db.fetch(
        f"""
        SELECT id, fact, category, confidence, edited, source_msg_id, created_at
        FROM memories
        {where}
        ORDER BY created_at DESC
        LIMIT 500
        """,
        *params,
    )
    return [dict(row) for row in rows]


@router.delete("/memories/{memory_id}")
async def delete_memory(memory_id: int) -> dict:
    ok = await db.execute("DELETE FROM memories WHERE id = $1", memory_id)
    if ok.startswith("DELETE 0"):
        raise HTTPException(status_code=404, detail="memory not found")
    return {"ok": True}


@router.put("/memories/{memory_id}")
async def update_memory(memory_id: int, body: MemoryPut) -> dict:
    fact = body.fact.strip()
    if not fact:
        raise HTTPException(status_code=400, detail="fact cannot be empty")
    existing = await db.fetchrow("SELECT id FROM memories WHERE id = $1", memory_id)
    if not existing:
        raise HTTPException(status_code=404, detail="memory not found")

    embedding = await retriever.embed_text(fact)
    if embedding is None:
        await db.execute(
            "UPDATE memories SET fact = $2, edited = TRUE WHERE id = $1",
            memory_id,
            fact,
        )
    else:
        await db.execute(
            """
            UPDATE memories
            SET fact = $2, edited = TRUE, embedding = $3, embed_model = $4
            WHERE id = $1
            """,
            memory_id,
            fact,
            embedding,
            retriever.EMBED_MODEL,
        )

    row = await db.fetchrow(
        "SELECT id, fact, category, confidence, edited FROM memories WHERE id = $1",
        memory_id,
    )
    return dict(row)


@router.post("/memories/forget_all")
async def forget_all() -> dict:
    deleted = await db.fetchval("SELECT count(*) FROM memories")
    await db.execute("TRUNCATE memories RESTART IDENTITY")
    return {"deleted": deleted}


@router.post("/memories/reextract")
async def reextract(body: ReextractRequest) -> dict:
    rows = await db.fetch(
        """
        SELECT id, role, content FROM messages
        WHERE conversation_id = $1
        ORDER BY id
        """,
        body.conversation_id,
    )
    pairs: list[tuple[str, str, int | None]] = []
    for i, row in enumerate(rows):
        if row["role"] != "user":
            continue
        assistant = None
        for nxt in rows[i + 1 :]:
            if nxt["role"] == "assistant":
                assistant = nxt["content"]
                break
        pairs.append((row["content"], assistant or "", row["id"]))
    for user, assistant, source_msg_id in pairs:
        await extractor.extract_later(user, assistant, source_msg_id)
    return {"processed": len(pairs)}
