"""Research runner: scheduler, per-run FSM pipeline, cancel, boot recovery.

Postgres is the queue and the checkpoint (PHASE_9 "Runner Internals"). One
asyncio.Task per run; a module-level scheduler pops `queued` runs FIFO while
under RESEARCH_MAX_CONCURRENT. The PLAN stage runs the real planner (SP-3);
research/write/verify still run the MOCK stages below until SP-4/SP-5 land.

Cancellation semantics: a user POST /cancel cancels the in-flight task, and the
pipeline's CancelledError handler persists `cancelled`. Shutdown cancellation
(stop_scheduler) does NOT mark runs cancelled — an api restart mid-run is a
crash, and boot recovery on the next start marks those runs `interrupted`.
"""

import asyncio
import contextlib
import json
import logging
import time
from typing import Optional

import db
from providers import registry
from providers.base import fmt_err
from research import events, llm, planner, researcher, store, verifier, writer
from research.config import RESEARCH_MAX_CONCURRENT

logger = logging.getLogger(__name__)

TERMINAL: frozenset[str] = frozenset(events.TERMINAL)

_active: dict[str, asyncio.Task] = {}
_scheduler: Optional[asyncio.Task] = None
_wake = asyncio.Event()
_cancel_requested: set[str] = set()


def _init_metrics() -> dict:
    return {
        "searches": 0,
        "fetches": 0,
        "fetch_failures": 0,
        "notes": 0,
        "iterations": 0,
        "tool_calls": 0,
        "tokens": {"prompt": 0, "completion": 0},
        "cost_usd": 0.0,
        "estimated": False,
        "llm_calls": 0,
        "stage_durations_s": {},
        "smart_source": "auto",
        "fast_source": "local",
    }


class _TrackingLLM:
    """Adapter over the llm module so every `complete()` call feeds
    ctx['metrics'] (tokens/cost) — the stages never accumulate usage themselves.
    `load_prompt`/`extract_json` are stateless and just forwarded."""

    def __init__(self, ctx: dict) -> None:
        self._ctx = ctx

    def load_prompt(self, name: str) -> str:
        return llm.load_prompt(name)

    def extract_json(self, text: str) -> dict | list | None:
        return llm.extract_json(text)

    async def complete(self, *args, **kwargs) -> dict:
        result = await llm.complete(*args, **kwargs)
        _merge_usage(
            self._ctx,
            result.get("usage") or {},
            result.get("provider_id"),
            result.get("model"),
        )
        return result


def _merge_usage(ctx: dict, usage: dict, provider_id, model) -> None:
    """Add one llm.complete result's usage to the running rollup. Local
    estimates (usage['estimated']) are counted but never priced (PHASE_9
    Metrics: chars/4 is the standard local path, cost is cloud-only)."""
    m = ctx["metrics"]
    m["llm_calls"] += 1
    m["tokens"]["prompt"] += int(usage.get("prompt_tokens") or 0)
    m["tokens"]["completion"] += int(usage.get("completion_tokens") or 0)
    if usage.get("estimated"):
        m["estimated"] = True
        return
    cost = registry.cost_for(provider_id, model, usage)
    if cost is not None:
        m["cost_usd"] += cost


async def _persist_metrics(run_id: str, ctx: dict, stage: str, duration_s: float) -> None:
    """Persist the running rollup and emit a `metrics` SSE event at a stage end."""
    ctx["metrics"]["stage_durations_s"][stage] = round(duration_s, 1)
    await store.update_run(run_id, metrics=ctx["metrics"])
    await events.append(run_id, "metrics", {"stage": stage, "metrics": ctx["metrics"]})


async def _chat_notice(conversation_id: int, content: str) -> None:
    """Insert a one-line research system message via the normal chat path
    (PHASE_9 chat integration). build_messages filters system rows out of the
    prompt (chat/context.py) — these are user-visible notices only."""
    await db.execute(
        "INSERT INTO messages (conversation_id, role, content) VALUES ($1, 'system', $2)",
        conversation_id,
        content,
    )


async def scheduler_loop() -> None:
    """Pop queued runs FIFO while capacity allows; sleep until woken or 2s."""
    while True:
        _wake.clear()
        try:
            while len(_active) < RESEARCH_MAX_CONCURRENT:
                row = await db.fetchrow(
                    """
                    SELECT id FROM research_runs
                    WHERE status = 'queued'
                    ORDER BY created_at, id
                    LIMIT 1
                    """
                )
                if row is None:
                    break
                start_run(str(row["id"]))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduler tick failed")
        try:
            await asyncio.wait_for(_wake.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is None or _scheduler.done():
        _scheduler = asyncio.create_task(scheduler_loop())


async def stop_scheduler() -> None:
    """Cancel the scheduler and any active runs (NOT marked cancelled — boot
    recovery marks them interrupted on the next start)."""
    global _scheduler
    tasks = list(_active.values())
    if _scheduler is not None and not _scheduler.done():
        tasks.append(_scheduler)
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    _scheduler = None
    _active.clear()
    _cancel_requested.clear()


async def run_pipeline(run_id: str) -> None:
    """FSM driver. Stage boundaries are `events.transition` calls.

    PLAN/RESEARCH/WRITE/VERIFY are real (planner, researcher loop, report
    writer, verifier). WRITE/VERIFY landed in SP-5.
    """
    stage = "boot"
    started = time.monotonic()
    try:
        run = await store.get_run(run_id)
        if run is None:
            return
        query = run["query"]
        config_json = run["config"]
        if isinstance(config_json, str):
            config_json = json.loads(config_json or "{}")

        stage = "plan"
        t_stage = time.monotonic()
        await events.transition(run_id, "planning")
        smart_override = config_json.get("smart_override")
        roles = await llm.resolve_roles(run["model_policy"], smart_override)
        if roles["smart"] is None:
            detail = "no eligible model for smart role"
            await events.transition(run_id, "failed", detail=detail)
            await events.append(
                run_id, "error", {"stage": "plan", "detail": detail, "retryable": True}
            )
            return
        smart_row, smart_model = roles["smart"]
        await store.update_run(run_id, provider_id=smart_row["id"], model=smart_model)
        ctx = {
            "run": run,
            "roles": roles,
            "config": config_json,
            "run_started": started,
            "docs": await store.get_run_docs(run_id),
        }
        ctx["metrics"] = _init_metrics()
        ctx["metrics"]["smart_source"] = "user" if smart_override else "auto"
        if roles["fast"] is None:
            roles["fast"] = roles["smart"]
            ctx["metrics"]["fast_source"] = "smart_fallback"
            await events.append(
                run_id,
                "error",
                {
                    "stage": "plan",
                    "tool": "fast_role",
                    "detail": (
                        f"no local model for fast role - using smart model "
                        f"{smart_model} for query-gen/notes"
                    ),
                    "retryable": True,
                },
            )
        ctx["llm"] = _TrackingLLM(ctx)
        ctx["plan"] = await planner.plan(ctx)
        await _persist_metrics(run_id, ctx, "plan", time.monotonic() - t_stage)

        stage = "research"
        t_stage = time.monotonic()
        await events.transition(run_id, "researching")
        await researcher.research_run(ctx)
        await _persist_metrics(run_id, ctx, "research", time.monotonic() - t_stage)
        counts = await store.run_counts(run_id)
        if int(counts["notes"]) == 0:
            if int(counts["sources"]) == 0:
                detail = "insufficient_sources"
            else:
                detail = "no_notes_extracted"
            await events.transition(run_id, "failed", detail=detail)
            await events.append(
                run_id, "error", {"stage": "research", "detail": detail, "retryable": True}
            )
            return
        await _persist_metrics(run_id, ctx, "research", time.monotonic() - t_stage)

        stage = "write"
        t_stage = time.monotonic()
        await events.transition(run_id, "writing")
        report_path = await writer.write_report(ctx)
        await _persist_metrics(run_id, ctx, "write", time.monotonic() - t_stage)

        stage = "verify"
        t_stage = time.monotonic()
        await events.transition(run_id, "verifying")
        await verifier.verify_report(ctx, report_path)
        await _persist_metrics(run_id, ctx, "verify", time.monotonic() - t_stage)

        await events.transition(run_id, "done")
        counts = await store.run_counts(run_id)
        metrics = ctx["metrics"]
        duration_s = round(time.monotonic() - started, 1)
        await events.append(
            run_id,
            "done",
            {
                "report_path": report_path,
                "sources": int(counts["sources"]),
                "duration_s": duration_s,
                "cost_usd": metrics["cost_usd"],
                "tokens": {
                    "prompt": metrics["tokens"]["prompt"],
                    "completion": metrics["tokens"]["completion"],
                },
            },
        )
        fresh = await store.get_run(run_id)
        if fresh is not None and fresh.get("conversation_id"):
            summary = (fresh.get("summary") or "").strip().replace("\n", " ")
            await _chat_notice(
                fresh["conversation_id"],
                f"RESEARCH DONE ▸ {fresh.get('title') or query[:80]} — "
                f"{summary[:200]} [run {run_id[:8]}]",
            )
    except asyncio.CancelledError:
        # User-initiated cancel (request_cancel) → persist `cancelled`. The
        # run keeps its transient status during a shutdown cancel so boot
        # recovery can mark it `interrupted` on the next start.
        if run_id in _cancel_requested:
            try:
                await events.transition(run_id, "cancelled", detail="cancelled by user")
            except ValueError:
                pass
        _cancel_requested.discard(run_id)
        raise
    except Exception as exc:
        logger.exception("run %s failed at stage %s", run_id, stage)
        try:
            await events.transition(run_id, "failed", detail=fmt_err(exc))
        except ValueError:
            pass
        try:
            await events.append(
                run_id,
                "error",
                {"stage": stage, "detail": fmt_err(exc), "retryable": False},
            )
        except Exception:
            logger.exception("run %s: could not record error event", run_id)


def start_run(run_id: str) -> None:
    task = asyncio.create_task(run_pipeline(run_id))
    _active[run_id] = task

    def _done(t: asyncio.Task) -> None:
        _active.pop(run_id, None)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error("run %s pipeline crashed: %s", run_id, exc)

    task.add_done_callback(_done)


async def request_cancel(run_id: str) -> str:
    """Cancel a run. Returns 'cancelling' (in-flight) or 'cancelled' (queued)."""
    task = _active.get(run_id)
    if task is not None and not task.done():
        _cancel_requested.add(run_id)
        task.cancel()
        return "cancelling"
    run = await store.get_run(run_id)
    if run is None:
        raise ValueError("run not found")
    if run["status"] == "queued":
        await events.transition(run_id, "cancelled", detail="cancelled before start")
        return "cancelled"
    raise ValueError("run not cancellable")


async def boot_recovery() -> None:
    """Mark every non-terminal run `interrupted` (api restarted mid-run)."""
    rows = await db.fetch(
        """
        SELECT id FROM research_runs
        WHERE status NOT IN ('done', 'failed', 'cancelled', 'interrupted')
        """
    )
    for row in rows:
        rid = str(row["id"])
        await events.force_status(rid, "interrupted", detail="api restarted")
        await events.append(
            rid, "error", {"stage": "boot", "detail": "api restarted", "retryable": True}
        )


async def resume_run(run_id: str) -> None:
    """Re-queue an interrupted/failed run; the scheduler picks it up."""
    run = await store.get_run(run_id)
    if run is None:
        raise ValueError("run not found")
    if run["status"] not in ("interrupted", "failed"):
        raise ValueError(f"run is {run['status']} — cannot resume")
    await events.transition(run_id, "queued", detail="resumed by user")
    _wake.set()