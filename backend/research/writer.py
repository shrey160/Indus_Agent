"""WRITE stage: outline -> per-section drafting -> stitched report file.

One smart role call for the outline (query + plan + task summaries), then one
smart call per section. Section input is ONLY the task-filtered, salience-ranked
notes bundle labelled with citation numbers [n] — raw page text never leaves the
researcher (PHASE_9: context budget + citation integrity). The numbered source
list is generated from `research_sources` ROWS, never LLM-authored (URLs can't
hallucinate). The report file is written with plain open() only (HP-022).

Streaming note: one `report.delta` event per section (batched). A per-chunk
stream would require a streaming variant of llm.complete to be plumbed through
the stage; per-section events are enough for the SSE contract and are the
upgrade path if finer progress granularity is ever wanted.
"""

import asyncio
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

from research import events, llm, store

logger = logging.getLogger(__name__)

# Mirror of main.DATA_DIR — importing `main` here is circular (main imports the
# research router which imports the runner which imports this). Same pattern as
# router.py; comment applies here too.
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))

_MAX_TITLE_SLUG = 40


def _slug(title: str) -> str:
    """Lowercase, non-alphanumeric -> '-', strip dashes, cap length."""
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return (slug or "report")[: _MAX_TITLE_SLUG]


async def _outline(ctx: dict) -> list[dict]:
    """One smart call: query + plan + task summaries -> ordered sections."""
    run = ctx["run"]
    query = run["query"]
    plan = ctx.get("plan") or {}
    tasks = await store.get_tasks(str(run["id"]))
    task_lines = "\n".join(
        f"{t['idx']} — {t['question']} — {t.get('summary') or 'no summary'}"
        for t in tasks
    )
    prompt = llm.load_prompt("outline.md")
    prompt = (
        prompt.replace("{query}", query)
        .replace(
            "{plan}",
            f"report title: {plan.get('report_title')}\n"
            f"assumptions: {plan.get('assumptions')}",
        )
        .replace("{tasks}", task_lines)[:8000]
    )
    smart = ctx["roles"]["smart"]
    result = await ctx["llm"].complete(
        smart[0],
        smart[1],
        [{"role": "system", "content": prompt}, {"role": "user", "content": query}],
    )
    parsed = ctx["llm"].extract_json(result["text"])
    sections: list[dict] = []
    raw = parsed.get("sections") if isinstance(parsed, dict) else None
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            idxs = item.get("task_idxs")
            if not isinstance(idxs, list) or not idxs:
                continue
            idxs = [int(i) for i in idxs if isinstance(i, int) or str(i).strip().isdigit()]
            if not idxs:
                continue
            sections.append(
                {
                    "id": str(item.get("id") or "").strip() or _slug(title),
                    "title": title,
                    "task_idxs": idxs,
                }
            )
    if not sections:
        # Light degraded fallback (planner-style): one section carrying every
        # task. Keeps the run alive on unparseable outline JSON.
        logger.warning("write: outline unparseable — single-section fallback")
        sections = [
            {
                "id": "report",
                "title": plan.get("report_title") or query[:80],
                "task_idxs": [t["idx"] for t in tasks],
            }
        ]
    return sections


async def notes_bundle(ctx: dict, task_idxs: list[int]) -> str:
    """Notes for a section's tasks, salience-ranked, labelled with [n], fit to
    the token budget. [U] marks a note from the user's own documents (no source)."""
    run_id = str(ctx["run"]["id"])
    cfg = ctx["config"]
    tasks = await store.get_tasks(run_id)
    task_by_idx = {t["idx"]: t for t in tasks}
    sources = await store.get_run_sources(run_id)
    n_by_source = {str(s["id"]): s["n"] for s in sources}

    notes: list[tuple[float, str]] = []
    for idx in task_idxs:
        task = task_by_idx.get(idx)
        if task is None:
            continue
        for note in await store.get_task_notes(str(task["id"])):
            label = n_by_source.get(str(note["source_id"])) if note["source_id"] else "U"
            notes.append(
                (float(note["salience"]), f"[{label}] {note['note']}")
            )
    notes.sort(key=lambda x: x[0], reverse=True)
    budget_chars = int(cfg.get("notes_token_budget", 1500)) * 4
    lines: list[str] = []
    used = 0
    for _salience, line in notes:
        if used + len(line) > budget_chars:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines) or "(no notes captured for this section)"


async def _draft_section(ctx: dict, section: dict, bundle: str) -> str:
    """One smart call per section -> markdown text (evented in the caller)."""
    prompt = llm.load_prompt("section.md")
    prompt = prompt.replace("{title}", section["title"]).replace("{notes}", bundle)[:8000]
    smart = ctx["roles"]["smart"]
    result = await ctx["llm"].complete(
        smart[0],
        smart[1],
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"Draft the section '{section['title']}'."},
        ],
    )
    text = result["text"].strip()
    if not text:
        text = f"*(no content drafted for this section — {section['title']})*"
    return text


async def _synthesis_limitations(ctx: dict) -> str:
    """Synthesis + limitations built from task summaries (grounded, no LLM)."""
    run_id = str(ctx["run"]["id"])
    tasks = await store.get_tasks(run_id)
    bullets = [
        f"- **Task {t['idx']}** ({t['status']}): {t.get('summary') or 'no summary'}"
        for t in tasks
    ]
    body = "\n".join(bullets)
    return (
        "## Synthesis & limitations\n\n"
        + (body + "\n\n" if body else "")
        + "This report was assembled from the notes taken during the research "
        "run. It reflects the sources fetched at the time of the run and may "
        "not cover the full topic; statements rest on the cited sources."
    )


async def _footer_line(ctx: dict) -> str:
    """Metrics meta line (PHASE_9 "Metrics & Cost": a footer in the report
    file). Appended at WRITE time — reflects usage accumulated so far through
    plan + research, priced only when the provider reports real usage."""
    run_id = str(ctx["run"]["id"])
    m = ctx.get("metrics") or {}
    tokens = m.get("tokens") or {}
    counts = await store.run_counts(run_id)
    elapsed = int(time.monotonic() - ctx.get("run_started", time.monotonic()))
    est = " (tokens estimated)" if m.get("estimated") else ""
    return (
        f"\n\n---\n*Model: {ctx['roles']['smart'][1]} · Depth: {ctx['run']['depth']} · "
        f"Sources: {int(counts['sources'])} · Duration: {elapsed}s · "
        f"Tokens: {tokens.get('prompt', 0)}/{tokens.get('completion', 0)}{est} · "
        f"Cost: ${float(m.get('cost_usd') or 0):.4f}*"
    )


async def _sources_section(ctx: dict) -> str:
    """Numbered source list from research_sources rows (persisted order)."""
    run_id = str(ctx["run"]["id"])
    sources = await store.get_run_sources(run_id)
    if not sources:
        return "## Sources\n\n*(no sources were retrieved)*"
    lines = ["## Sources", ""]
    for s in sources:
        title = s.get("title") or s.get("domain") or s["url"]
        line = f"[{s['n']}] {title} — {s.get('domain') or ''} — {s['url']}"
        if s["fetch_status"] == "failed":
            line += " (fetch failed)"
        lines.append(f"{s['n']}. {line}")
    return "\n".join(lines)


async def _flush_report(
    ctx: dict, sections_text: list[str], *, cancelled: bool = False
) -> str:
    """Stitch the report (completed sections or the partial set), write it to
    /data, persist report_path/summary, and return the relative path.

    `cancelled=True` is the partial flush of the PHASE_9 Cancel path: whatever
    sections were drafted are preserved under an `INCOMPLETE — cancelled`
    header — a cancelled run keeps its partial report.
    """
    run = ctx["run"]
    run_id = str(run["id"])
    query = run["query"]
    plan = ctx.get("plan") or {}
    depth = run["depth"]

    title = plan.get("report_title") or query[:80]
    smart = ctx["roles"]["smart"]
    meta = [
        f"# {title}",
        "",
        f"- **Query:** {query}",
        f"- **Depth:** {depth} · **Model:** {smart[1]} · **Date:** {datetime.now().strftime('%Y-%m-%d')}",
    ]
    if cancelled:
        meta.append("- **INCOMPLETE — cancelled**")
    elif ctx.get("partial") or ctx.get("budget_hit") == "wall_clock":
        meta.append("- **PARTIAL — time budget exhausted**")

    assumptions = plan.get("assumptions") or []
    if assumptions:
        meta.append("- **Assumptions:** " + "; ".join(str(a) for a in assumptions))

    head = "\n".join(meta)
    intro = (
        f"## Introduction\n\n{plan.get('understanding') or query}\n\n"
        if plan.get("understanding")
        else ""
    )
    body = "\n\n".join(sections_text) if sections_text else "*(no sections were drafted)*"

    report = "\n\n".join(
        [head, intro + body, await _synthesis_limitations(ctx), await _sources_section(ctx)]
    )
    report += await _footer_line(ctx)

    stamp = datetime.now()
    rel_dir = f"research/{stamp.strftime('%Y-%m')}"
    abs_dir = Path(DATA_DIR) / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)
    rel_path = f"{rel_dir}/{_slug(title)}-{run_id[:8]}.md"
    abs_path = Path(DATA_DIR) / rel_path
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(report)

    await store.update_run(run_id, report_path=rel_path, summary=report[:500])
    logger.info(
        "write: run %s -> %s (%d chars, cancelled=%s)",
        run_id,
        rel_path,
        len(report),
        cancelled,
    )
    return rel_path


async def write_report(ctx: dict) -> str:
    """Full WRITE stage. Returns the report's relative path (e.g.
    research/YYYY-MM/slug-<id8>.md) under DATA_DIR.

    Cancellation (PHASE_9 "Cancel"): the section loop is a stage boundary —
    on CancelledError the sections drafted so far are flushed as a partial
    report marked `INCOMPLETE — cancelled`, then the exception is re-raised so
    the runner records the `cancelled` transition (the status change belongs to
    the runner, never here). The flush is best-effort: it must not mask the
    cancellation itself.
    """
    run_id = str(ctx["run"]["id"])

    sections_text: list[str] = []
    try:
        sections = await _outline(ctx)
        ctx["sections"] = sections  # verifier re-uses the outline mapping
        for section in sections:
            bundle = await notes_bundle(ctx, section["task_idxs"])
            text = await _draft_section(ctx, section, bundle)
            sections_text.append(f"## {section['title']}\n\n{text}")
            await events.append(
                run_id, "report.delta", {"section": section["title"], "text": text}
            )
    except asyncio.CancelledError:
        try:
            await _flush_report(ctx, sections_text, cancelled=True)
        except Exception:
            logger.exception("write: partial flush on cancel failed for run %s", run_id)
        raise
    return await _flush_report(ctx, sections_text)
