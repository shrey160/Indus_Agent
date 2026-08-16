import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db
from backup import EXPORTS_DIR

router = APIRouter(prefix="/api", tags=["retention"])


class RetentionIn(BaseModel):
    retention_months: int | None = None


async def _get_retention_months() -> int | None:
    return await db.fetchval(
        "SELECT retention_months FROM app_state WHERE id = TRUE"
    )


@router.get("/settings/retention")
async def get_retention() -> dict:
    return {"retention_months": await _get_retention_months()}


@router.put("/settings/retention")
async def put_retention(body: RetentionIn) -> dict:
    months = body.retention_months
    if months is not None and not 1 <= months <= 120:
        raise HTTPException(400, "retention_months must be 1-120 or null")
    await db.execute(
        "UPDATE app_state SET retention_months = $1 WHERE id = TRUE", months
    )
    return {"retention_months": months}


@router.post("/retention/archive")
async def archive_old_conversations() -> dict:
    months = await _get_retention_months()
    if months is None:
        raise HTTPException(400, "retention off")

    conv_rows = await db.fetch(
        """
        SELECT id, title, created_at FROM conversations
        WHERE created_at < now() - make_interval(months => $1)
        ORDER BY id
        """,
        months,
    )
    if not conv_rows:
        return {"archived": 0, "file": None}

    conv_ids = [row["id"] for row in conv_rows]
    msg_rows = await db.fetch(
        """
        SELECT id, conversation_id, role, content, model, created_at, cost_usd,
               sources, tool_events, reasoning
        FROM messages
        WHERE conversation_id = ANY($1::int[])
        ORDER BY id
        """,
        conv_ids,
    )

    conversations = []
    for row in conv_rows:
        item = dict(row)
        item["created_at"] = item["created_at"].isoformat()
        conversations.append(item)
    messages = []
    for row in msg_rows:
        item = dict(row)
        item["created_at"] = item["created_at"].isoformat()
        if item["cost_usd"] is not None:
            item["cost_usd"] = float(item["cost_usd"])
        for key in ("sources", "tool_events"):
            value = item.get(key)
            item[key] = json.loads(value) if value else None
        messages.append(item)

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"archive-{stamp}.json"
    (EXPORTS_DIR / name).write_text(
        json.dumps({"conversations": conversations, "messages": messages}, indent=2),
        encoding="utf-8",
    )

    await db.execute(
        "DELETE FROM conversations WHERE id = ANY($1::int[])", conv_ids
    )
    return {"archived": len(conv_ids), "file": name}


@router.post("/maintenance/vacuum")
async def vacuum() -> dict:
    pool = db.get_pool()
    async with pool.acquire() as conn:
        await conn.execute("VACUUM ANALYZE")
    return {"ok": True}
