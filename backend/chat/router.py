import asyncio
import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import db
from agent import loop as agent_loop
from memory import extractor
from mcp_client import manager as mcp_manager
from providers import registry
from providers.base import ProviderHTTPError, fmt_err
from . import context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


def schedule_extraction(user_text: str, assistant_text: str, message_id: int | None) -> None:
    try:
        asyncio.create_task(
            extractor.extract_later(user_text, assistant_text, message_id)
        )
    except Exception:
        logger.warning("failed to schedule memory extraction", exc_info=True)


class ChatRequest(BaseModel):
    conversation_id: int | None = None
    message: str


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


@router.post("/chat")
async def chat(body: ChatRequest):
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

    conversation_id = body.conversation_id
    if conversation_id is None:
        conversation_id = await db.fetchval(
            "INSERT INTO conversations (title) VALUES ($1) RETURNING id",
            body.message[:60],
        )
    else:
        exists = await db.fetchval(
            "SELECT 1 FROM conversations WHERE id = $1", conversation_id
        )
        if not exists:
            conversation_id = await db.fetchval(
                "INSERT INTO conversations (title) VALUES ($1) RETURNING id",
                body.message[:60],
            )
    await db.execute(
        "INSERT INTO messages (conversation_id, role, content) VALUES ($1, 'user', $2)",
        conversation_id,
        body.message,
    )
    messages = await context.build_messages(conversation_id)
    provider = registry.build_provider(provider_row)

    async def stream():
        full = ""
        final_text = None
        sources: list[dict] = []
        usage = None
        is_cloud = provider_row["kind"] == "cloud"
        try:
            async for event in agent_loop.run(
                provider, model, messages, mcp_manager, native_tools=is_cloud
            ):
                etype = event["type"]
                if etype == "reasoning":
                    yield sse({"reasoning": event["text"]})
                elif etype == "content":
                    full += event["text"]
                    yield sse({"delta": event["text"]})
                elif etype == "usage":
                    usage = event["usage"]
                elif etype == "tool":
                    yield sse({"tool": event["tool"]})
                elif etype == "tool_limit":
                    yield sse({"tool_limit": event["detail"]})
                elif etype == "final":
                    final_text = event["text"]
                    sources = event["sources"]
            cost = registry.cost_for(provider_row["id"], model, usage)
        except asyncio.CancelledError:
            if full:
                cost = registry.cost_for(provider_row["id"], model, usage)
                message_id = await db.fetchval(
                    """
                    INSERT INTO messages (conversation_id, role, content, model, cost_usd)
                    VALUES ($1, 'assistant', $2, $3, $4)
                    RETURNING id
                    """,
                    conversation_id,
                    full,
                    model,
                    cost,
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
                INSERT INTO messages (conversation_id, role, content, model, cost_usd)
                VALUES ($1, 'assistant', $2, $3, $4)
                RETURNING id
                """,
                conversation_id,
                reply,
                model,
                cost,
            )
            schedule_extraction(body.message, reply, message_id)
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
        "SELECT id, title, created_at FROM conversations ORDER BY id DESC LIMIT 50"
    )
    return [dict(row) for row in rows]


@router.get("/conversations/{conversation_id}/messages")
async def conversation_messages(conversation_id: int) -> list[dict]:
    rows = await db.fetch(
        """
        SELECT role, content, model, created_at FROM messages
        WHERE conversation_id = $1
        ORDER BY id
        """,
        conversation_id,
    )
    return [dict(row) for row in rows]


@router.post("/conversations", status_code=201)
async def create_conversation() -> dict:
    row = await db.fetchrow(
        "INSERT INTO conversations DEFAULT VALUES RETURNING id, title, created_at"
    )
    return dict(row)
