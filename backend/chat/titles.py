import asyncio
import logging

import db
from memory import extractor

logger = logging.getLogger(__name__)

TITLE_PROMPT = """Write a short title (6 words max, no quotes, no punctuation at the end) for a conversation that starts with this user message:

{msg}"""


async def maybe_title(conversation_id: int, first_user_msg: str) -> None:
    try:
        title = await db.fetchval(
            "SELECT title FROM conversations WHERE id = $1", conversation_id
        )
        if title != "New chat":
            return

        picked = await extractor._pick_extract_provider()
        if picked is None:
            return
        provider_id, model = picked
        row = await db.fetchrow("SELECT * FROM providers WHERE id = $1", provider_id)
        if row is None:
            return

        text = await extractor._complete(
            row, model, TITLE_PROMPT.format(msg=first_user_msg[:800])
        )
        if not text:
            return

        cleaned = text.strip().split("\n")[0].strip().strip('"').strip("'")
        cleaned = cleaned[:60]
        if not cleaned:
            return

        await db.execute(
            "UPDATE conversations SET title = $2 WHERE id = $1 AND title = 'New chat'",
            conversation_id,
            cleaned,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("auto-title failed for conversation %s", conversation_id, exc_info=True)


def _done_callback(task: asyncio.Task) -> None:
    exc = task.exception()
    if exc:
        logger.exception("background auto-title task failed: %s", exc)


def kickoff_title(conversation_id: int, first_user_msg: str) -> None:
    try:
        task = asyncio.create_task(maybe_title(conversation_id, first_user_msg))
        task.add_done_callback(_done_callback)
    except Exception:
        logger.warning("failed to schedule auto-title", exc_info=True)
