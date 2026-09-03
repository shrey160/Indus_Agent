import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import db
from agent import loop as agent_loop
from backup import is_restoring
from memory import extractor
from mcp_client import manager as mcp_manager
from providers import registry
from providers.base import ProviderHTTPError, fmt_err
from . import attachments as attachments_mod
from . import context
from .titles import kickoff_title

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


def schedule_extraction(user_text: str, assistant_text: str, message_id: int | None) -> None:
    try:
        asyncio.create_task(
            extractor.extract_later(user_text, assistant_text, message_id)
        )
    except Exception:
        logger.warning("failed to schedule memory extraction", exc_info=True)


class Attachment(BaseModel):
    name: str
    ext: str
    size: int = 0
    chars: int = 0
    text: str


class ChatRequest(BaseModel):
    conversation_id: int | None = None
    message: str
    attachments: list[Attachment] | None = None


class PatchTitle(BaseModel):
    title: str


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def resolve_active() -> tuple[dict | None, str | None, str | None, str | None]:
    state = await db.fetchrow(
        """
        SELECT s.active_model, p.id, p.name, p.base_url, p.type, p.kind, p.key_hint, p.api_key_enc
        FROM app_state s
        JOIN providers p ON p.id = s.active_provider_id
        WHERE s.id = TRUE
        """
    )
    if not state or not state["active_model"]:
        return None, None, "no_model", None
    statuses = await registry.detect_all()
    status = statuses.get(state["id"])
    if status is None:
        return None, None, "provider_down", state["name"]
    if status.state == "down" or status.state == "unreachable":
        return None, None, "provider_down", state["name"]
    if status.state == "bad_key":
        return dict(state), None, "bad_key", "invalid API key for " + state["name"]
    if status.state == "no_credits":
        return dict(state), None, "no_credits", "no credits for " + state["name"]
    return dict(state), state["active_model"], None, None


@router.post("/extract")
async def extract(file: UploadFile):
    data = await file.read()
    if len(data) > attachments_mod.ATTACH_MAX_BYTES:
        limit_mb = attachments_mod.ATTACH_MAX_BYTES // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"file too large (max {limit_mb}MB)")
    try:
        return attachments_mod.extract_upload(file.filename or "", data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/chat")
async def chat(body: ChatRequest):
    if is_restoring():
        raise HTTPException(503, "restoring backup")
    provider_row, model, gate_error, gate_detail = await resolve_active()
    if gate_error is not None:
        async def gated():
            yield sse(
                {
                    "error": gate_error,
                    "detail": gate_detail,
                    "provider_id": provider_row["id"] if provider_row else None,
                    "provider_name": provider_row["name"] if provider_row else None,
                }
            )

        return StreamingResponse(gated(), media_type="text/event-stream")

    docs = None
    if body.attachments:
        try:
            docs = attachments_mod.validate_docs(
                [a.model_dump() for a in body.attachments]
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if not body.message.strip() and not docs:
        raise HTTPException(status_code=400, detail="message is required")

    conversation_id = body.conversation_id
    if conversation_id is None:
        conversation_id = await db.fetchval(
            "INSERT INTO conversations DEFAULT VALUES RETURNING id"
        )
    else:
        exists = await db.fetchval(
            "SELECT 1 FROM conversations WHERE id = $1", conversation_id
        )
        if not exists:
            conversation_id = await db.fetchval(
                "INSERT INTO conversations DEFAULT VALUES RETURNING id"
            )
    await db.execute(
        "INSERT INTO messages (conversation_id, role, content, attachments) VALUES ($1, 'user', $2, $3)",
        conversation_id,
        body.message,
        json.dumps(docs) if docs else None,
    )
    messages, auto_sources = await context.build_messages(conversation_id)
    provider = registry.build_provider(provider_row)

    async def stream():
        full = ""
        final_text = None
        sources: list[dict] = []
        tool_log: list[dict] = []
        reasoning_full = ""
        usage = None
        is_cloud = provider_row["kind"] == "cloud"
        try:
            async for event in agent_loop.run(
                provider, model, messages, mcp_manager, native_tools=is_cloud
            ):
                etype = event["type"]
                if etype == "reasoning":
                    reasoning_full += event["text"]
                    yield sse({"reasoning": event["text"]})
                elif etype == "content":
                    full += event["text"]
                    yield sse({"delta": event["text"]})
                elif etype == "usage":
                    usage = event["usage"]
                elif etype == "tool":
                    tool_event = event["tool"]
                    if tool_event.get("status") == "running":
                        tool_log.append(
                            {
                                "name": tool_event["name"],
                                "args": tool_event.get("args"),
                                "status": "running",
                            }
                        )
                    elif tool_event.get("status") == "done":
                        done = {
                            "name": tool_event["name"],
                            "status": "done",
                            "latency_ms": tool_event.get("latency_ms"),
                            "result_preview": tool_event.get("result_preview"),
                            "error": tool_event.get("error"),
                        }
                        merged = False
                        for entry in reversed(tool_log):
                            if entry.get("name") == tool_event["name"]:
                                entry.update(done)
                                merged = True
                                break
                        if not merged and tool_log:
                            tool_log[0].update(done)
                        elif not tool_log:
                            tool_log.append(done)
                    yield sse({"tool": tool_event})
                elif etype == "tool_limit":
                    yield sse({"tool_limit": event["detail"]})
                elif etype == "final":
                    final_text = event["text"]
                    sources = auto_sources + event["sources"]
            cost = registry.cost_for(provider_row["id"], model, usage)
        except asyncio.CancelledError:
            if full:
                cost = registry.cost_for(provider_row["id"], model, usage)
                message_id = await db.fetchval(
                    """
                    INSERT INTO messages (conversation_id, role, content, model, cost_usd, sources, tool_events, reasoning)
                    VALUES ($1, 'assistant', $2, $3, $4, $5, $6, $7)
                    RETURNING id
                    """,
                    conversation_id,
                    full,
                    model,
                    cost,
                    json.dumps(sources),
                    json.dumps(tool_log),
                    reasoning_full,
                )
                schedule_extraction(body.message, full, message_id)
            raise
        except ProviderHTTPError as exc:
            code = {401: "bad_key", 402: "no_credits", 429: "rate_limited"}.get(
                exc.status_code, "stream_interrupted"
            )
            logger.warning("chat stream failed (%s): %s", code, exc.detail)
            yield sse(
                {
                    "error": code,
                    "detail": exc.detail,
                    "provider_id": provider_row["id"],
                    "provider_name": provider_row["name"],
                }
            )
            return
        except Exception as exc:
            logger.warning("chat stream failed: %s", fmt_err(exc))
            yield sse({"error": "stream_interrupted", "detail": fmt_err(exc)})
            return
        reply = final_text if final_text else full
        if reply:
            cost = registry.cost_for(provider_row["id"], model, usage)
            message_id = await db.fetchval(
                """
                INSERT INTO messages (conversation_id, role, content, model, cost_usd, sources, tool_events, reasoning)
                VALUES ($1, 'assistant', $2, $3, $4, $5, $6, $7)
                RETURNING id
                """,
                conversation_id,
                reply,
                model,
                cost,
                json.dumps(sources),
                json.dumps(tool_log),
                reasoning_full,
            )
            schedule_extraction(body.message, reply, message_id)
            msg_count = await db.fetchval(
                "SELECT count(*) FROM messages WHERE conversation_id = $1",
                conversation_id,
            )
            if msg_count == 2:
                kickoff_title(conversation_id, body.message)
            yield sse(
                {
                    "done": True,
                    "message_id": message_id,
                    "conversation_id": conversation_id,
                    "cost_usd": cost,
                    "cloud": is_cloud,
                    "sources": sources,
                    "usage": (
                        {
                            "prompt": usage.get("prompt_tokens"),
                            "completion": usage.get("completion_tokens"),
                            "total": usage.get("total_tokens"),
                            "reasoning": usage.get("reasoning_tokens"),
                        }
                        if usage
                        else None
                    ),
                    "context_length": registry.context_length(
                        provider_row["id"], model
                    ),
                }
            )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/conversations")
async def list_conversations() -> list[dict]:
    rows = await db.fetch(
        """
        SELECT c.id, c.title, c.created_at, count(m.id) AS message_count
        FROM conversations c
        LEFT JOIN messages m ON m.conversation_id = c.id
        GROUP BY c.id
        ORDER BY c.id DESC
        LIMIT 200
        """
    )
    return [dict(row) for row in rows]


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(conversation_id: int, body: PatchTitle) -> dict:
    title = body.title.strip()
    if not title or len(title) > 120:
        raise HTTPException(status_code=400, detail="title must be 1-120 characters")
    ok = await db.execute(
        "UPDATE conversations SET title = $2 WHERE id = $1",
        conversation_id,
        title,
    )
    if ok.startswith("UPDATE 0"):
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"id": conversation_id, "title": title}


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: int) -> dict:
    ok = await db.execute("DELETE FROM conversations WHERE id = $1", conversation_id)
    if ok.startswith("DELETE 0"):
        raise HTTPException(status_code=404, detail="conversation not found")
    return {"ok": True}


@router.get("/conversations/{conversation_id}/messages")
async def conversation_messages(conversation_id: int) -> list[dict]:
    rows = await db.fetch(
        """
        SELECT role, content, model, created_at, sources, tool_events, reasoning, attachments
        FROM messages
        WHERE conversation_id = $1
        ORDER BY id
        """,
        conversation_id,
    )
    result = []
    for row in rows:
        item = dict(row)
        for key in ("sources", "tool_events", "attachments"):
            value = item.get(key)
            item[key] = json.loads(value) if value else None
        result.append(item)
    return result


@router.post("/conversations", status_code=201)
async def create_conversation() -> dict:
    row = await db.fetchrow(
        "INSERT INTO conversations DEFAULT VALUES RETURNING id, title, created_at"
    )
    return dict(row)
