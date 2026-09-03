import json
import logging
import os

import db
from memory import retriever, soul
from memory.budget import approx_tokens, budget_breakdown, trim_history
from rag import retriever as rag_retriever

logger = logging.getLogger(__name__)

HISTORY_FETCH_LIMIT = 60

REINJECT_TURNS = int(os.environ.get("ATTACH_REINJECT_TURNS", "2"))

CITE_INSTRUCTION = "Cite sources using [n] when using <context> or rag.search results."


async def build_messages(conversation_id: int) -> tuple[list[dict], list[dict]]:
    # Additive guard (PHASE_9 chat integration): research run notices are
    # stored as role='system' rows — never forward them to the LLM.
    rows = await db.fetch(
        """
        SELECT role, content, attachments FROM messages
        WHERE conversation_id = $1
          AND role IN ('user', 'assistant')
        ORDER BY id DESC
        LIMIT $2
        """,
        conversation_id,
        HISTORY_FETCH_LIMIT,
    )
    rows = list(reversed(rows))

    current_user = rows[-1]["content"] if rows and rows[-1]["role"] == "user" else ""

    # PHASE_10: Last-N attachment re-injection. asyncpg returns jsonb as str.
    # Full file text only for the newest REINJECT_TURNS attachment-bearing user
    # turns; older turns collapse to a marker line. current_user stays the clean
    # typed text so memory/RAG retrieval never embeds file text.
    history: list[dict] = []
    for r in rows:
        atts = None
        if r["role"] == "user" and r["attachments"]:
            atts = r["attachments"]
            if isinstance(atts, str):
                atts = json.loads(atts)
        history.append({"role": r["role"], "content": r["content"], "atts": atts or None})

    att_turns = [h for h in history if h["atts"]]
    for i, h in enumerate(att_turns):
        newest = i >= len(att_turns) - REINJECT_TURNS
        for a in h["atts"]:
            if newest:
                h["content"] += f"\n\n[ATTACHED FILE: {a['name']} — {a['chars']} chars]\n{a['text']}"
            else:
                h["content"] += f"\n\n[ATTACHED FILE: {a['name']} — no longer in context]"

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
    last = history[-1] if history else None
    user_tok = approx_tokens(
        last["content"] if last and last["role"] == "user" else current_user
    )
    breakdown = budget_breakdown(system_tok, user_tok)
    logger.debug("budget breakdown: %s", breakdown)

    trimmed = trim_history(
        [{"role": h["role"], "content": h["content"]} for h in history],
        breakdown["history_budget"],
    )
    messages.extend(trimmed)
    return messages, rag_sources
