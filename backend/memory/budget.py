import logging

logger = logging.getLogger(__name__)

SOUL_BUDGET = 300
MEMORY_BUDGET = 500
RAG_BUDGET = 1500
COMPLETION_RESERVE = 1024
DEFAULT_CONTEXT = 8192


def approx_tokens(text: str) -> int:
    if not text:
        return 0
    return (len(text) + 3) // 4


def fit(text: str, budget: int) -> str:
    if approx_tokens(text) <= budget:
        return text
    return text[: budget * 4]


def trim_history(messages: list[dict], budget: int) -> list[dict]:
    out = list(messages)
    while len(out) > 1:
        total = approx_tokens("".join(m.get("content", "") for m in out))
        if total <= budget:
            break
        del out[0]
        while len(out) > 1 and out[0].get("role") != "user":
            del out[0]
    logger.debug(
        "history budget %d tok: kept %d/%d messages",
        budget,
        len(out),
        len(messages),
    )
    return out


def budget_breakdown(system_tok: int, user_tok: int) -> dict:
    history_budget = max(
        DEFAULT_CONTEXT - COMPLETION_RESERVE - system_tok - user_tok, 0
    )
    return {
        "context": DEFAULT_CONTEXT,
        "completion_reserve": COMPLETION_RESERVE,
        "soul_tok": system_tok,
        "user_tok": user_tok,
        "history_budget": history_budget,
        "rag_reserved": RAG_BUDGET,
    }
