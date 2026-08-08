"""PLAN stage: turn the user query into an orthogonal task plan (smart role).

One LLM call with prompts/plan.md, one retry with a stricter suffix on
unparseable JSON, then MANDATORY degraded fallback (PHASE_9 "Planner degraded
mode"): a single-task plan beats a failed run. Normalized/clamped plan rows are
persisted via store.insert_tasks + update_run, and a `plan` event is appended.
"""

import logging

from research import events, llm, store

logger = logging.getLogger(__name__)

_STRICT_SUFFIX = "Reply with ONLY the JSON object, no prose, no markdown fences."
_MAX_TITLE = 120
_MAX_QUESTION = 500


def _normalize_plan(raw: dict, query: str, max_tasks: int) -> dict:
    """Harden an LLM plan dict: clamps task count, re-numbers idx 1..N, forces
    kind='research', guarantees usable strings. Never raises."""
    understanding = str(raw.get("understanding") or query).strip() or query
    assumptions = raw.get("assumptions")
    if not isinstance(assumptions, list):
        assumptions = [str(assumptions)] if assumptions else []
    assumptions = [str(a)[:200] for a in assumptions]
    report_title = (str(raw.get("report_title") or "").strip() or query[:80])[:_MAX_TITLE]
    use_rag = bool(raw.get("use_rag"))

    raw_tasks = raw.get("tasks")
    if not isinstance(raw_tasks, list):
        raw_tasks = []
    tasks = []
    for i, item in enumerate(raw_tasks[:max_tasks], start=1):
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()[:_MAX_QUESTION]
        if not question:
            continue
        angles = item.get("angles")
        if not isinstance(angles, list):
            angles = []
        tasks.append(
            {
                "idx": i,
                "question": question,
                "kind": "research",
                "angles": [str(a)[:200] for a in angles],
            }
        )
    if not tasks:
        tasks = [{"idx": 1, "question": query[:_MAX_QUESTION], "kind": "research", "angles": []}]
    return {
        "understanding": understanding,
        "assumptions": assumptions,
        "tasks": tasks,
        "use_rag": use_rag,
        "report_title": report_title,
    }


async def plan(ctx: dict) -> dict:
    """Run the PLAN stage. ctx: {"run", "roles", "config", "llm"} from the
    runner. Returns the normalized plan dict; persists tasks + plan/title."""
    complete = ctx["llm"].complete
    run = ctx["run"]
    run_id = str(run["id"])
    query = run["query"]
    max_tasks = int(ctx["config"].get("tasks", 3))
    smart_row, smart_model = ctx["roles"]["smart"]

    prompt = llm.load_prompt("plan.md").replace("{max_tasks}", str(max_tasks))
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": query},
    ]

    parsed: dict | None = None
    degraded_reason: str | None = None
    for attempt in range(2):
        msgs = messages
        if attempt == 1:
            msgs = list(messages) + [{"role": "user", "content": _STRICT_SUFFIX}]
        result = await complete(smart_row, smart_model, msgs)
        candidate = llm.extract_json(result["text"])
        if isinstance(candidate, dict) and candidate.get("tasks"):
            parsed = candidate
            break
        logger.debug("plan: attempt %d unparseable (%r)", attempt + 1, result["text"][:200])

    if parsed is None:
        degraded_reason = "unparseable plan JSON"
        await events.append(run_id, "plan.degraded", {"reason": degraded_reason})
        parsed = {
            "understanding": query,
            "assumptions": ["planner output unparseable — single-task fallback"],
            "tasks": [{"idx": 1, "question": query, "kind": "research", "angles": []}],
            "use_rag": False,
            "report_title": query[:80],
        }

    plan_json = _normalize_plan(parsed, query, max_tasks)
    await store.insert_tasks(run_id, plan_json["tasks"])
    await store.update_run(
        run_id, plan=plan_json, title=plan_json["report_title"]
    )
    await events.append(
        run_id,
        "plan",
        {
            "tasks": [
                {"idx": t["idx"], "question": t["question"]} for t in plan_json["tasks"]
            ],
            "assumptions": plan_json["assumptions"],
            "title": plan_json["report_title"],
        },
    )
    logger.info("plan: run %s -> %d tasks (degraded=%s)", run_id, len(plan_json["tasks"]), bool(degraded_reason))
    return plan_json