import asyncio
import json
import logging
import re

import db
from providers import registry
from .retriever import COSINE_DUP, EMBED_MODEL, cosine, embed_text

logger = logging.getLogger(__name__)

CATEGORIES = {"preference", "identity", "project", "general"}
CONFIDENCE_STEP = 0.1
CONFIDENCE_MAX = 2.0
FACT_MAX_CHARS = 200

PROMPT_TMPL = """You maintain a memory store about the user. From the exchange below, extract NEW \
durable facts (preferences, identity, projects, constraints). Rules:
- durable only; ignore one-off requests, moods, questions
- each fact <= 20 words, atomic, third person
- return JSON: {{"facts": [{{"fact": "...", "category": "preference|identity|project|general"}}]}}
  or {{"facts": []}}
EXCHANGE: user: {user} assistant: {assistant}"""


MIN_LOCAL_MODEL_BYTES = 1_000_000


def _is_real_local_model(provider_type: str, model) -> bool:
    if provider_type == "ollama":
        if ":cloud" in model.id or "embed" in model.id:
            return False
        if model.size_bytes is not None and model.size_bytes < MIN_LOCAL_MODEL_BYTES:
            return False
    return True


def _model_size(model) -> int:
    return model.size_bytes if model.size_bytes is not None else 2**63


async def _pick_extract_provider() -> tuple[int, str] | None:
    statuses = await registry.detect_all()
    rows = await db.fetch("SELECT id, type, kind FROM providers ORDER BY id")
    candidates: list[tuple[int, str, int, str]] = []
    for row in rows:
        if row["kind"] == "cloud":
            continue
        status = statuses.get(row["id"])
        if status is None or status.state != "up" or not status.models:
            continue
        pool = [m for m in status.models if _is_real_local_model(row["type"], m)]
        if not pool:
            continue
        smallest = min(pool, key=_model_size)
        candidates.append((row["id"], smallest.id, _model_size(smallest), row["type"]))
    if not candidates:
        return None
    prefer = [c for c in candidates if c[3] == "openai"] or candidates
    best = min(prefer, key=lambda c: c[2])
    return best[0], best[1]


async def _complete(row, model: str, prompt: str) -> str:
    provider = registry.build_provider(row)
    text = ""
    async for event in provider.stream_chat(
        model, [{"role": "user", "content": prompt}]
    ):
        if event["type"] == "content":
            text += event["text"]
    return text


def _parse_facts(text: str) -> list[dict]:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        logger.warning("memory extractor: no JSON block in reply")
        return []
    try:
        data = json.loads(match.group(0))
    except Exception as exc:
        logger.warning("memory extractor: invalid JSON: %s", exc)
        return []
    facts = data.get("facts") or []
    out: list[dict] = []
    for item in facts:
        if not isinstance(item, dict):
            continue
        fact = str(item.get("fact", "")).strip()
        category = str(item.get("category", "general")).strip()
        if not fact or len(fact) > FACT_MAX_CHARS:
            continue
        if category not in CATEGORIES:
            category = "general"
        out.append({"fact": fact, "category": category})
    return out


async def _bump_confidence(fact_id: int, source_msg_id: int | None) -> None:
    await db.execute(
        """
        UPDATE memories
        SET confidence = LEAST(confidence + $1, $2),
            source_msg_id = COALESCE($3, source_msg_id)
        WHERE id = $4
        """,
        CONFIDENCE_STEP,
        CONFIDENCE_MAX,
        source_msg_id,
        fact_id,
    )


async def _insert_fact(fact: str, category: str, source_msg_id: int | None) -> str:
    existing = await db.fetchrow(
        "SELECT id FROM memories WHERE lower(fact) = lower($1)",
        fact,
    )
    if existing:
        await _bump_confidence(existing["id"], source_msg_id)
        return "merged"

    embedding = await embed_text(fact)
    if embedding:
        rows = await db.fetch(
            "SELECT id, embedding FROM memories WHERE embedding IS NOT NULL"
        )
        for row in rows:
            if cosine(embedding, row["embedding"]) > COSINE_DUP:
                await _bump_confidence(row["id"], source_msg_id)
                return "merged_similar"

    await db.execute(
        """
        INSERT INTO memories (fact, category, embedding, embed_model, confidence, source_msg_id)
        VALUES ($1, $2, $3, $4, 1.0, $5)
        """,
        fact,
        category,
        embedding,
        EMBED_MODEL if embedding else None,
        source_msg_id,
    )
    return "inserted"


async def run_extraction(
    user_text: str,
    assistant_text: str,
    source_msg_id: int | None = None,
) -> dict:
    picked = await _pick_extract_provider()
    if picked is None:
        logger.info("memory extraction skipped: no up local provider")
        return {"status": "skipped", "reason": "no up local provider"}
    provider_id, model = picked
    row = await db.fetchrow("SELECT * FROM providers WHERE id = $1", provider_id)
    if row is None:
        return {"status": "skipped", "reason": "provider vanished"}
    prompt = PROMPT_TMPL.format(
        user=(user_text or "")[:2000], assistant=(assistant_text or "")[:4000]
    )
    raw = await _complete(row, model, prompt)
    if not raw:
        return {"status": "no_facts", "provider": provider_id, "model": model}
    facts = _parse_facts(raw)
    if not facts:
        return {"status": "no_facts", "provider": provider_id, "model": model}
    results = []
    for fact in facts:
        results.append(
            await _insert_fact(fact["fact"], fact["category"], source_msg_id)
        )
    logger.info(
        "memory extraction (%s/%s): %d facts -> %s",
        provider_id,
        model,
        len(facts),
        results,
    )
    return {"status": "ok", "provider": provider_id, "model": model, "facts": results}


async def extract_later(
    user_text: str, assistant_text: str, source_msg_id: int | None = None
) -> None:
    try:
        await run_extraction(user_text, assistant_text, source_msg_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("memory extraction failed", exc_info=True)
