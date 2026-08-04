import db

HISTORY_LIMIT = 20


def soul_block() -> str | None:
    return None


def memory_block() -> str | None:
    return None


async def build_messages(conversation_id: int) -> list[dict]:
    rows = await db.fetch(
        """
        SELECT role, content FROM messages
        WHERE conversation_id = $1
        ORDER BY id DESC
        LIMIT $2
        """,
        conversation_id,
        HISTORY_LIMIT,
    )
    messages: list[dict] = []
    system_parts = [block for block in (soul_block(), memory_block()) if block]
    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})
    messages.extend(
        {"role": row["role"], "content": row["content"]} for row in reversed(rows)
    )
    return messages
