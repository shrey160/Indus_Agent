import logging

import db
from memory import retriever, soul
from memory.budget import approx_tokens, budget_breakdown, trim_history

logger = logging.getLogger(__name__)

HISTORY_FETCH_LIMIT = 60


async def build_messages(conversation_id: int) -> list[dict]:
    rows = await db.fetch(
        """
        SELECT role, content FROM messages
        WHERE conversation_id = $1
        ORDER BY id DESC
        LIMIT $2
        """,
        conversation_id,
        HISTORY_FETCH_LIMIT,
    )
    rows = list(reversed(rows))

    current_user = rows[-1]["content"] if rows and rows[-1]["role"] == "user" else ""

    system_parts: list[str] = []
    persona = soul.soul_block()
    if persona:
        system_parts.append(persona)
    memory = await retriever.memory_block(current_user)
    if memory:
        system_parts.append(memory)

    messages: list[dict] = []
    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})

    system_tok = approx_tokens("\n\n".join(system_parts))
    user_tok = approx_tokens(current_user)
    breakdown = budget_breakdown(system_tok, user_tok)
    logger.debug("budget breakdown: %s", breakdown)

    history = [{"role": r["role"], "content": r["content"]} for r in rows]
    trimmed = trim_history(history, breakdown["history_budget"])
    messages.extend(trimmed)
    return messages
