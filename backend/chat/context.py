import logging

import db
from memory import retriever, soul
from memory.budget import approx_tokens, budget_breakdown, trim_history
from rag import retriever as rag_retriever

logger = logging.getLogger(__name__)

HISTORY_FETCH_LIMIT = 60

CITE_INSTRUCTION = "Cite sources using [n] when using <context> or rag.search results."


async def build_messages(conversation_id: int) -> tuple[list[dict], list[dict]]:
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

    rag_sources: list[dict] = []
    rag_auto = await db.fetchval("SELECT rag_auto FROM app_state WHERE id = TRUE")
    docs_ready = await db.fetchval(
        "SELECT EXISTS(SELECT 1 FROM documents WHERE status = 'ready')"
    )
    if rag_auto and docs_ready:
        try:
            block, rag_sources = await rag_retriever.rag_context(current_user)
            if block:
                system_parts.append(block)
                joined = "\n\n".join(system_parts)
                if CITE_INSTRUCTION not in joined:
                    system_parts.append(CITE_INSTRUCTION)
        except Exception:
            logger.warning("failed to build rag context", exc_info=True)

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
    return messages, rag_sources
