import json
import logging
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

import db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["dataset"])


def _dataset_line(conversations: list[dict]) -> str:
    return json.dumps({"conversations": conversations}, ensure_ascii=False)


@router.get("/dataset/export")
async def export_dataset(
    format: str = "jsonl",
    exclude_tools: bool = True,
    min_turns: int = 2,
) -> FileResponse:
    if format != "jsonl":
        raise HTTPException(400, "unsupported format")
    if min_turns < 2:
        raise HTTPException(400, "min_turns must be >= 2")

    conv_rows = await db.fetch(
        """
        SELECT c.id, c.title, count(m.id) AS cnt
        FROM conversations c
        JOIN messages m ON m.conversation_id = c.id
        GROUP BY c.id
        HAVING count(m.id) >= $1
        ORDER BY c.id
        """,
        min_turns,
    )
    conv_ids = [row["id"] for row in conv_rows]
    msg_rows = await db.fetch(
        """
        SELECT conversation_id, role, content, tool_events
        FROM messages
        WHERE conversation_id = ANY($1::int[])
        ORDER BY id
        """,
        conv_ids,
    )

    by_conv: dict[int, list[dict]] = {}
    for row in msg_rows:
        by_conv.setdefault(row["conversation_id"], []).append(row)

    tmp = tempfile.TemporaryDirectory(prefix="dataset-")
    tmp_path = Path(tmp.name)
    dataset = tmp_path / "conversations.jsonl"
    try:
        with dataset.open("w", encoding="utf-8") as fh:
            for conv_id in conv_rows:
                rows = by_conv.get(conv_id["id"], [])
                if exclude_tools and any(
                    r["tool_events"] is not None and r["role"] == "assistant" for r in rows
                ):
                    continue
                conversations = []
                for row in rows:
                    content = (row["content"] or "").strip()
                    if row["role"] == "user":
                        if content:
                            conversations.append({"from": "human", "value": content})
                    elif row["role"] == "assistant":
                        if content:
                            conversations.append({"from": "gpt", "value": content})
                if any(c["from"] == "human" for c in conversations) and any(
                    c["from"] == "gpt" for c in conversations
                ):
                    fh.write(_dataset_line(conversations) + "\n")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
        name = f"local-ai-hub-dataset-{stamp}.jsonl"
        return FileResponse(
            dataset,
            filename=name,
            media_type="application/x-ndjson",
            background=BackgroundTask(tmp.cleanup),
        )
    except Exception:
        tmp.cleanup()
        raise