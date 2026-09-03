"""RESEARCH stage: per-task query-gen -> search -> fetch -> notes -> reflect.

Tool calls go through tools_registry.call_tool wrapped in asyncio.wait_for
(agent/loop.py pattern) — we never import toolbox code (PHASE_9 "Researcher").
Raw page text is consumed here; only structured notes flow downstream (context
budget + citation integrity). Wall-clock and tool-call caps are checked at the
runner (between tasks) and here (before every call) — a cap force-closes the
current task via reflect-with-what-exists, never raises. Rag hits become notes
with source_id=NULL (the notes table has no meta column — spec DDL is verbatim;
a NULL source marks "from the user's own documents").
"""

import asyncio
import logging
import time
from urllib.parse import urlsplit

from mcp_client import manager as mcp_manager
from providers.base import fmt_err
from research import events, llm, store
from tools_registry import call_tool

logger = logging.getLogger(__name__)

CACHE_TTL_H = 24
MAX_QUERIES = 4
MAX_NOTES_PER_PAGE = 6
NOTE_MAX_CHARS = 500
EXCERPT_CHARS = 600
SUMMARY_MAX_CHARS = 1000


def _domain(url: str) -> str:
    return urlsplit(url).netloc.lower()


async def call_tool_timeout(name: str, args: dict, timeout_s: float) -> dict:
    return await asyncio.wait_for(
        call_tool(mcp_manager, name, args), timeout=timeout_s
    )


async def _tool_failed(ctx: dict, tool: str, exc: Exception) -> None:
    """Record one run-level `error` event per failing tool per run (AC-5
    degradation matrix: the error event must name the tool call that failed).
    Per-item failures are tolerated — this is an audit trail, not an abort."""
    key = f"_tool_err_{tool}"
    if ctx.get(key):
        return
    ctx[key] = True
    run_id = str(ctx["run"]["id"])
    await events.append(
        run_id,
        "error",
        {
            "stage": "research",
            "tool": tool,
            "detail": fmt_err(exc)[:300],
            "retryable": True,
        },
    )


async def research_run(ctx: dict) -> None:
    """RESEARCH stage driver: loop tasks in idx order, enforce the wall-clock
    deadline between tasks, then close the current task and skip the rest."""
    run_id = str(ctx["run"]["id"])
    cfg = ctx["config"]
    ctx.setdefault(
        "metrics",
        {
            "searches": 0,
            "fetches": 0,
            "fetch_failures": 0,
            "notes": 0,
            "iterations": 0,
            "tool_calls": 0,
        },
    )
    ctx["budget_hit"] = None
    ctx["io_sem"] = asyncio.Semaphore(int(cfg.get("search_concurrency", 3)))
    ctx["run_started"] = time.monotonic()

    tasks = await store.get_tasks(run_id)
    for i, task in enumerate(tasks):
        if ctx["budget_hit"]:
            await store.set_task(str(task["id"]), "skipped")
            continue
        elapsed = time.monotonic() - ctx["run_started"]
        deadline_s = int(cfg.get("wall_min", 10)) * 60
        if elapsed >= deadline_s:
            await events.append(
                run_id,
                "budget.exhausted",
                {
                    "guard": "wall_clock",
                    "elapsed_s": round(elapsed, 1),
                    "deadline_s": deadline_s,
                },
            )
            ctx["budget_hit"] = "wall_clock"
            await _close_task(ctx, task)
            for rest in tasks[i + 1 :]:
                await store.set_task(str(rest["id"]), "skipped")
            break
        await research_task(ctx, task)


async def research_task(ctx: dict, task: dict) -> None:
    """One task: query-gen -> (search+fetch+notes) loop -> reflect -> done."""
    run_id = str(ctx["run"]["id"])
    task_id = str(task["id"])
    idx = task["idx"]
    question = task["question"]
    cfg = ctx["config"]
    await events.append(run_id, "task.start", {"idx": idx, "question": question})
    await store.set_task(task_id, "running")

    ctx["sources_added"] = 0
    queries = await _querygen(ctx, task)
    if ctx.get("docs"):
        # SP-Q3: doc notes land BEFORE search so seed instructions are always
        # captured — LLM-only, no tool budget consumed, survives a blown budget.
        await _docs_pass(ctx, task)
    reflect: dict = {}
    iterations = 0
    try:
        while True:
            await _search_and_fetch(ctx, task, queries)
            iterations += 1
            ctx["metrics"]["iterations"] += 1
            reflect = await _reflect(ctx, task, iterations)
            coverage = reflect.get("coverage", "sufficient")
            followup = reflect.get("followup_queries") or []
            if (
                coverage == "insufficient"
                and iterations < int(cfg.get("iterations", 1))
                and not ctx["budget_hit"]
                and followup
            ):
                queries = [str(q)[:300] for q in followup if str(q).strip()][:MAX_QUERIES]
                if queries:
                    continue
            break
    finally:
        pass  # task is closed below in all paths (budget, wall-clock, normal)

    await _close_task(ctx, task, reflect=reflect, iterations=iterations)


async def _querygen(ctx: dict, task: dict) -> list[str]:
    """Generate 2-4 diverse search queries via the fast role; fall back to the
    question itself when the role is missing or the JSON won't parse."""
    run_id = str(ctx["run"]["id"])
    question = task["question"]
    prompt = llm.load_prompt("querygen.md")
    prompt = (
        prompt.replace("{question}", question)
        .replace("{angles}", ", ".join(str(a) for a in (task.get("angles") or [])) or "none")
        .replace(
            "{gaps}",
            ", ".join(str(g) for g in ctx.get("gaps", [])) or "none",
        )
    )
    fast = ctx["roles"].get("fast")
    if fast is None:
        logger.warning("querygen: no fast role — using question as query")
        return [question]
    try:
        result = await ctx["llm"].complete(fast[0], fast[1], [
            {"role": "system", "content": prompt},
            {"role": "user", "content": question},
        ])
    except Exception as exc:
        logger.warning("querygen failed for task %s: %s", task["idx"], fmt_err(exc))
        await _tool_failed(ctx, "llm.querygen", exc)
        return [question]
    parsed = ctx["llm"].extract_json(result["text"])
    queries = []
    if isinstance(parsed, dict):
        raw = parsed.get("queries")
        if isinstance(raw, list):
            queries = [str(q)[:300] for q in raw if str(q).strip()][:MAX_QUERIES]
    return queries or [question]


async def _search_and_fetch(ctx: dict, task: dict, queries: list[str]) -> None:
    """Search each query, dedupe against seen+fresh-cache, fetch top-K unabread
    candidates, and extract notes from each fetched page inline."""
    cfg = ctx["config"]
    run_id = str(ctx["run"]["id"])
    idx = task["idx"]
    metrics = ctx["metrics"]
    seen = await store.seen_urls(run_id)
    cache_fresh = await store.fresh_cache_urls(CACHE_TTL_H)

    for q in queries:
        if ctx["budget_hit"] or metrics["tool_calls"] >= int(cfg["tool_calls"]):
            ctx["budget_hit"] = "tool_calls"
            break
        try:
            # NOTE: 30s timeout per SP-4; searxng itself caps at 15s.
            async with ctx["io_sem"]:
                result = await call_tool_timeout(
                    "web.search", {"query": q, "max_results": 8}, 30.0
                )
        except Exception as exc:
            metrics["searches"] += 1
            metrics["tool_calls"] += 1
            logger.warning("web.search failed for %r: %s", q, fmt_err(exc))
            await _tool_failed(ctx, "web.search", exc)
            await events.append(
                run_id,
                "search",
                {"idx": idx, "query": q, "results": 0, "error": fmt_err(exc)[:200]},
            )
            continue
        metrics["searches"] += 1
        metrics["tool_calls"] += 1
        results = result.get("results") or []
        await events.append(
            run_id, "search", {"idx": idx, "query": q, "results": len(results)}
        )

        candidates = []
        for item in results:
            url = (item.get("url") or "").strip()
            if not url:
                continue
            canonical = store.canonicalize_url(url)
            if canonical in seen or canonical in cache_fresh:
                continue
            seen.add(canonical)
            candidates.append(
                {
                    "url": canonical,
                    "title": item.get("title"),
                    "query": q,
                }
            )

        remaining = int(cfg["sources_per_task"]) - ctx["sources_added"]
        for cand in candidates[: max(0, remaining)]:
            if ctx["budget_hit"] or metrics["tool_calls"] >= int(cfg["tool_calls"]):
                ctx["budget_hit"] = "tool_calls"
                break
            await _fetch_one(ctx, task, cand)

    plan = ctx.get("plan") or {}
    if (plan.get("academic") or False) and not ctx["budget_hit"]:
        await _arxiv_pass(ctx, task, seen, cache_fresh)
    if (plan.get("use_rag") or False) and not ctx["budget_hit"]:
        await _rag_pass(ctx, task)


async def _fetch_one(ctx: dict, task: dict, cand: dict) -> None:
    cfg = ctx["config"]
    metrics = ctx["metrics"]
    run_id = str(ctx["run"]["id"])
    idx = task["idx"]
    url = cand["url"]
    try:
        async with ctx["io_sem"]:
            result = await call_tool_timeout(
                "web.fetch",
                {
                    "url": url,
                    "max_chars": int(cfg["page_chars"]),
                    "extract": "auto",
                    "cache_ttl_hours": CACHE_TTL_H,
                },
                int(cfg.get("fetch_timeout_s", 20)),
            )
        metrics["fetches"] += 1
        metrics["tool_calls"] += 1
    except Exception as exc:
        metrics["fetches"] += 1
        metrics["tool_calls"] += 1
        metrics["fetch_failures"] += 1
        logger.warning("web.fetch failed for %s: %s", url, fmt_err(exc))
        await _tool_failed(ctx, "web.fetch", exc)
        try:
            await store.add_source(
                run_id,
                url,
                cand["title"],
                _domain(url),
                None,
                "failed",
                {"query": cand["query"]},
            )
        except Exception:
            logger.exception("add_source(failed) crashed for %s", url)
        await events.append(
            run_id,
            "fetch",
            {"idx": idx, "url": url, "ok": False, "error": fmt_err(exc)[:200]},
        )
        return

    fetched_url = result.get("url") or url
    source = await store.add_source(
        run_id,
        fetched_url,
        result.get("title") or cand["title"],
        _domain(fetched_url),
        (result.get("text") or "")[:EXCERPT_CHARS],
        "cached" if result.get("cached") else "ok",
        {"query": cand["query"]},
    )
    ctx["sources_added"] += 1
    await events.append(
        run_id,
        "source.added",
        {
            "n": source["n"],
            "url": fetched_url,
            "title": source["title"],
            "domain": source["domain"],
        },
    )
    await events.append(
        run_id,
        "fetch",
        {
            "idx": idx,
            "url": fetched_url,
            "ok": True,
            "chars": int(result.get("chars") or 0),
            "cached": bool(result.get("cached")),
        },
    )
    await _extract_notes(
        ctx, task, str(source["id"]), fetched_url, source["title"], result.get("text") or ""
    )


async def _arxiv_pass(ctx: dict, task: dict, seen: set[str], cache_fresh: set[str]) -> None:
    """One arxiv.search per task when the plan marks the topic academic.
    Results are NOT fetched — abstracts go straight to the note path."""
    cfg = ctx["config"]
    metrics = ctx["metrics"]
    run_id = str(ctx["run"]["id"])
    if metrics["tool_calls"] >= int(cfg["tool_calls"]):
        ctx["budget_hit"] = "tool_calls"
        return
    try:
        async with ctx["io_sem"]:
            result = await call_tool_timeout(
                "arxiv.search", {"query": task["question"], "max_results": 5}, 30.0
            )
    except Exception as exc:
        logger.warning("arxiv.search failed for task %s: %s", task["idx"], fmt_err(exc))
        await _tool_failed(ctx, "arxiv.search", exc)
        return
    metrics["tool_calls"] += 1
    for item in result.get("results") or []:
        url = (item.get("url") or "").strip()
        if not url:
            continue
        canonical = store.canonicalize_url(url)
        if canonical in seen or canonical in cache_fresh:
            continue
        seen.add(canonical)
        abstract = item.get("abstract") or ""
        source = await store.add_source(
            run_id,
            canonical,
            item.get("title"),
            "arxiv.org",
            abstract[:EXCERPT_CHARS],
            "ok",
            {"arxiv": True},
        )
        await events.append(
            run_id,
            "source.added",
            {
                "n": source["n"],
                "url": canonical,
                "title": source["title"],
                "domain": "arxiv.org",
            },
        )
        if abstract:
            await _extract_notes(
                ctx, task, str(source["id"]), canonical, source["title"], abstract
            )


async def _rag_pass(ctx: dict, task: dict) -> None:
    """One rag.search per task when the plan sets use_rag=true; hits become
    notes with source_id=NULL (marked by a doc tag in the note text)."""
    run_id = str(ctx["run"]["id"])
    task_id = str(task["id"])
    try:
        async with ctx["io_sem"]:
            result = await call_tool_timeout(
                "rag.search", {"query": task["question"], "top_k": 5}, 30.0
            )
    except Exception as exc:
        logger.warning("rag.search failed for task %s: %s", task["idx"], fmt_err(exc))
        await _tool_failed(ctx, "rag.search", exc)
        return
    for hit in result.get("results") or []:
        snippet = str(hit.get("snippet") or "")[: NOTE_MAX_CHARS - 60]
        doc = hit.get("doc") or "?"
        if not snippet:
            continue
        note = f"from user document {doc}: {snippet}"
        await store.add_note(run_id, task_id, None, note[:NOTE_MAX_CHARS], 0.5)
        ctx["metrics"]["notes"] += 1


async def _extract_notes(
    ctx: dict,
    task: dict,
    source_id: str | None,
    url: str,
    title: str,
    page_text: str,
    note_prefix: str = "",
) -> None:
    """One fast-role call per page -> clamped, stored notes."""
    cfg = ctx["config"]
    run_id = str(ctx["run"]["id"])
    task_id = str(task["id"])
    fast = ctx["roles"].get("fast")
    if fast is None or not page_text.strip():
        return
    prompt = llm.load_prompt("notes.md")
    prompt = (
        prompt.replace("{question}", task["question"])
        .replace("{title}", (title or url)[:200])
        .replace("{text}", page_text[: int(cfg["page_chars"])])
    )
    try:
        result = await ctx["llm"].complete(
            fast[0],
            fast[1],
            [{"role": "system", "content": prompt}, {"role": "user", "content": "Extract the notes."}],
        )
    except Exception as exc:
        logger.warning("notes extraction failed for %s: %s", url, fmt_err(exc))
        await _tool_failed(ctx, "llm.notes", exc)
        return
    parsed = ctx["llm"].extract_json(result["text"])
    items = parsed if isinstance(parsed, list) else ([parsed] if isinstance(parsed, dict) else [])
    for item in items[:MAX_NOTES_PER_PAGE]:
        if not isinstance(item, dict):
            continue
        note = str(item.get("note") or "").strip()[:NOTE_MAX_CHARS]
        if not note:
            continue
        try:
            salience = float(item.get("salience") or 0.5)
        except (TypeError, ValueError):
            salience = 0.5
        salience = min(1.0, max(0.0, salience))
        stored = f"{note_prefix}{note}"
        await store.add_note(run_id, task_id, source_id, stored[:NOTE_MAX_CHARS], salience)
        ctx["metrics"]["notes"] += 1


async def _docs_pass(ctx: dict, task: dict) -> None:
    """Feed the run's ephemeral docs through the notes path (source_id=NULL,
    `from user document <name>` prefix — same convention as _rag_pass).
    LLM-only: no tool-call budget consumed; llm_calls land in metrics via
    _TrackingLLM. Never raises — fast is None returns early per doc and LLM
    failures surface through _tool_failed (llm.notes)."""
    docs = ctx.get("docs") or []
    for doc in docs:
        name = str(doc.get("name") or "?")
        await _extract_notes(
            ctx,
            task,
            None,
            "",
            name,
            str(doc.get("text") or ""),
            note_prefix=f"from user document {name}: ",
        )


async def _reflect(ctx: dict, task: dict, iteration: int) -> dict:
    """Smart-role reflect: coverage/gaps/summary over this task's notes."""
    cfg = ctx["config"]
    run_id = str(ctx["run"]["id"])
    idx = task["idx"]
    notes = await store.get_task_notes(str(task["id"]))
    smart = ctx["roles"].get("smart")
    if not notes or smart is None:
        return {
            "coverage": "insufficient" if not notes else "sufficient",
            "gaps": ["no notes produced"] if not notes else [],
            "followup_queries": [],
            "summary": "no usable notes — task closed without a summary",
        }
    budget_chars = int(cfg.get("notes_token_budget", 1500)) * 4
    notes_text = "\n".join(f"- {n['note']}" for n in notes)[:budget_chars]
    prompt = llm.load_prompt("reflect.md")
    prompt = (
        prompt.replace("{question}", task["question"])
        .replace("{notes}", notes_text)
    )
    try:
        result = await ctx["llm"].complete(
            smart[0],
            smart[1],
            [{"role": "system", "content": prompt}, {"role": "user", "content": "Reflect on this task."}],
        )
    except Exception as exc:
        logger.warning("reflect failed for task %s: %s", idx, fmt_err(exc))
        return {
            "coverage": "sufficient",
            "gaps": [],
            "followup_queries": [],
            "summary": "reflect unavailable — task closed from notes",
        }
    parsed = ctx["llm"].extract_json(result["text"])
    reflect = parsed if isinstance(parsed, dict) else {}
    coverage = (
        reflect.get("coverage")
        if reflect.get("coverage") in ("sufficient", "insufficient")
        else "sufficient"
    )
    gaps_raw = reflect.get("gaps")
    gaps = [str(g)[:200] for g in gaps_raw][:10] if isinstance(gaps_raw, list) else []
    fq_raw = reflect.get("followup_queries")
    followup = (
        [str(q)[:300] for q in fq_raw if str(q).strip()][:MAX_QUERIES]
        if isinstance(fq_raw, list)
        else []
    )
    summary = str(reflect.get("summary") or "")[:SUMMARY_MAX_CHARS]
    await events.append(
        run_id,
        "reflect",
        {"idx": idx, "iteration": iteration, "coverage": coverage, "gaps": gaps},
    )
    return {
        "coverage": coverage,
        "gaps": gaps,
        "followup_queries": followup,
        "summary": summary,
    }


async def _close_task(
    ctx: dict, task: dict, reflect: dict | None = None, iterations: int = 0
) -> None:
    """Finish a task with a summary (reflect-with-what-exists on force-close)."""
    run_id = str(ctx["run"]["id"])
    idx = task["idx"]
    if ctx.get("budget_hit") == "tool_calls" and not ctx.get("_toolcap_evented"):
        # Hard-cap force-close: every guard exhaustion is logged as an event
        # (PHASE_9 Guards). The wall-clock equivalent fires in research_run.
        ctx["_toolcap_evented"] = True
        elapsed = time.monotonic() - ctx.get("run_started", time.monotonic())
        await events.append(
            run_id,
            "budget.exhausted",
            {
                "guard": "tool_calls",
                "tool_calls": int(ctx["metrics"]["tool_calls"]),
                "elapsed_s": round(elapsed, 1),
            },
        )
    if reflect is None:
        reflect = await _reflect(ctx, task, 0)
    if not reflect.get("summary"):
        reflect["summary"] = "task closed early (budget exhausted)"
    summary = reflect["summary"] or f"no summary produced for task {idx}"
    await store.set_task(
        str(task["id"]), "done", summary=summary[:SUMMARY_MAX_CHARS], iterations=iterations
    )
    counts = await store.run_counts(run_id)
    await events.append(
        run_id,
        "task.done",
        {
            "idx": idx,
            "summary": summary[:400],
            "sources": int(counts["sources"]),
            "notes": int(counts["notes"]),
        },
    )