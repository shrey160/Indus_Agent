import math

import httpx

import db
from .budget import MEMORY_BUDGET, approx_tokens

EMBED_MODEL = "nomic-embed-text"
EMBED_TIMEOUT = 10.0
SIM_WEIGHT = 0.7
RECENCY_WEIGHT = 0.3
COSINE_DUP = 0.9


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def embed_text(text: str) -> list[float] | None:
    rows = await db.fetch(
        "SELECT base_url FROM providers WHERE type = 'ollama' ORDER BY id"
    )
    if not rows:
        return None
    base = rows[0]["base_url"].rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=EMBED_TIMEOUT) as client:
            response = await client.post(
                f"{base}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": text},
            )
            response.raise_for_status()
        embedding = response.json().get("embedding")
        return list(embedding) if embedding else None
    except Exception:
        return None


def _recency_decay(created_at) -> float:
    import datetime

    if created_at is None:
        return 0.0
    now = datetime.datetime.now(datetime.timezone.utc)
    while created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=datetime.timezone.utc)
    age_days = max((now - created_at).total_seconds() / 86400.0, 0.0)
    return 1.0 / (1.0 + age_days)


async def memory_block(user_message: str) -> str:
    rows = await db.fetch(
        """
        SELECT fact, category, embedding, created_at
        FROM memories
        ORDER BY created_at DESC
        LIMIT 500
        """
    )
    if not rows:
        return ""
    query_vec = await embed_text(user_message)
    scored: list[tuple[float, str, str]] = []
    for row in rows:
        sim = cosine(query_vec, row["embedding"]) if query_vec else 0.0
        recency = _recency_decay(row["created_at"])
        score = SIM_WEIGHT * sim + RECENCY_WEIGHT * recency
        if score > 0:
            scored.append((score, row["fact"], row["category"]))
    scored.sort(key=lambda t: t[0], reverse=True)

    lines: list[str] = []
    used = 0
    for _, fact, category in scored:
        line = f"- {fact} [{category}]"
        tok = approx_tokens(line)
        if used + tok > MEMORY_BUDGET:
            break
        lines.append(line)
        used += tok
    if not lines:
        return ""
    return "<memory>\n" + "\n".join(lines) + "\n</memory>"
