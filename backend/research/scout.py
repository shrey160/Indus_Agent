"""Scout round: a search-only preliminary pass that grounds the PLAN stage.

Runs 2-3 fast-role queries through web.search BEFORE the planner's LLM call and
returns a compact digest (title + snippet + url) of what is actually out there,
so sub-questions are grounded in real entity names and current terminology
instead of model priors (human-requested SP-Q2 refinement of the PLAN stage).

Search-only by design: searxng snippets are enough for grounding and cost 2-3
tool calls, never fetches. The scout NEVER fails the run: every failure path
(fast role missing, search errors, zero results, tool-call budget already
spent) degrades to returning None and the planner plans from the raw query
alone — exactly the pre-fix behavior.
"""

import asyncio
import logging
from urllib.parse import urlsplit

from providers.base import fmt_err
from research import events, store
from research.researcher import (
    _domain,
    _querygen,
    _tool_failed,
    call_tool_timeout,
)

logger = logging.getLogger(__name__)

MAX_SCOUT_QUERIES = 3
MAX_SCOUT_RESULTS = 10
SNIPPET_CHARS = 300


def _build_digest(items: list[dict]) -> str:
    lines = ["PRELIMINARY WEB RESULTS (scout round - grounding only):"]
    for i, item in enumerate(items[:MAX_SCOUT_RESULTS], start=1):
        lines.append(f"[{i}] {item['title']} - {item['domain']}")
        if item["snippet"]:
            lines.append(item["snippet"])
        lines.append(item["url"])
    return "\n".join(lines)


async def gather_context(ctx: dict) -> str | None:
    """Search-only preliminary round; returns a digest for the planner prompt
    or None when nothing usable was found. Never raises."""
    run_id = str(ctx["run"]["id"])
    query = ctx["run"]["query"]
    cfg = ctx["config"]
    metrics = ctx["metrics"]

    queries = await _querygen(ctx, {"idx": 0, "question": query, "angles": []})
    queries = [str(q).strip() for q in queries if str(q).strip()][:MAX_SCOUT_QUERIES]
    if not queries:
        return None

    io_sem = ctx.setdefault(
        "io_sem", asyncio.Semaphore(int(cfg.get("search_concurrency", 3)))
    )
    seen = await store.seen_urls(run_id)
    items: list[dict] = []

    for q in queries:
        if metrics["tool_calls"] >= int(cfg["tool_calls"]):
            break
        try:
            async with io_sem:
                result = await call_tool_timeout(
                    "web.search", {"query": q, "max_results": 8}, 30.0
                )
        except Exception as exc:
            metrics["searches"] += 1
            metrics["tool_calls"] += 1
            logger.warning("scout: web.search failed for %r: %s", q, fmt_err(exc))
            await _tool_failed(ctx, "web.search", exc)
            await events.append(
                run_id,
                "search",
                {"idx": 0, "query": q, "results": 0, "error": fmt_err(exc)[:200]},
            )
            continue
        metrics["searches"] += 1
        metrics["tool_calls"] += 1
        results = result.get("results") or []
        await events.append(
            run_id, "search", {"idx": 0, "query": q, "results": len(results)}
        )
        for item in results:
            url = (item.get("url") or "").strip()
            if not url:
                continue
            canonical = store.canonicalize_url(url)
            if canonical in seen:
                continue
            seen.add(canonical)
            snippet = str(item.get("snippet") or "").replace("\x00", "")[:SNIPPET_CHARS]
            items.append(
                {
                    "title": str(item.get("title") or "").replace("\x00", "")[:200],
                    "domain": _domain(canonical),
                    "snippet": snippet,
                    "url": canonical,
                }
            )

    if not items:
        await events.append(run_id, "scout", {"queries": len(queries), "results": 0})
        return None

    await events.append(
        run_id,
        "scout",
        {"queries": len(queries), "results": len(items)},
    )
    logger.info(
        "scout: run %s -> %d queries, %d usable results",
        run_id,
        len(queries),
        len(items),
    )
    return _build_digest(items)
