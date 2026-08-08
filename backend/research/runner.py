"""Research runner: scheduler, per-run FSM pipeline, cancel, boot recovery.

Postgres is the queue and the checkpoint (PHASE_9 "Runner Internals"). One
asyncio.Task per run; a module-level scheduler pops `queued` runs FIFO while
under RESEARCH_MAX_CONCURRENT. Stage functions are imported lazily so missing
pieces (real planner/researcher/writer/verifier from SP-3/4/5) stay inert —
until then the pipeline runs the MOCK stages below.

Cancellation semantics: a user POST /cancel cancels the in-flight task, and the
pipeline's CancelledError handler persists `cancelled`. Shutdown cancellation
(stop_scheduler) does NOT mark runs cancelled — an api restart mid-run is a
crash, and boot recovery on the next start marks those runs `interrupted`.
"""

import asyncio
import contextlib
import logging
import time
from typing import Optional

import db
from providers.base import fmt_err
from research import events, store
from research.config import RESEARCH_MAX_CONCURRENT

logger = logging.getLogger(__name__)

TERMINAL: frozenset[str] = frozenset(events.TERMINAL)

_active: dict[str, asyncio.Task] = {}
_scheduler: Optional[asyncio.Task] = None
_wake = asyncio.Event()
_cancel_requested: set[str] = set()


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

    SP-2 stages are MOCK (sleeps + fake payloads) so the SSE contract is
    testable before any LLM exists; SP-3/4/5 replace them with the real stages.
    """
    stage = "boot"
    started = time.monotonic()
    try:
        run = await store.get_run(run_id)
        if run is None:
            return
        query = run["query"]

        stage = "plan"
        await events.transition(run_id, "planning")
        await asyncio.sleep(1)
        await events.append(
            run_id,
            "plan",
            {
                "tasks": [{"idx": 1, "question": query}],
                "assumptions": ["mock pipeline (SP-2) — no LLM involved"],
                "title": "Mock report",
            },
        )
        await store.insert_tasks(
            run_id, [{"idx": 1, "question": query, "kind": "research"}]
        )
        await store.update_run(run_id, title="Mock report")

        stage = "research"
        await events.transition(run_id, "researching")
        tasks = await store.get_tasks(run_id)
        for task in tasks:
            await events.append(
                run_id, "task.start", {"idx": task["idx"], "question": task["question"]}
            )
            await asyncio.sleep(1)
            await store.set_task(
                str(task["id"]), "done", summary="mock summary", iterations=1
            )
            await events.append(
                run_id,
                "task.done",
                {"idx": task["idx"], "summary": "mock summary", "sources": 0, "notes": 0},
            )

        stage = "write"
        await events.transition(run_id, "writing")
        await events.append(
            run_id, "report.delta", {"section": "Intro", "text": "Mock report intro."}
        )
        await asyncio.sleep(1)
        await events.append(
            run_id,
            "report.delta",
            {"section": "Synthesis", "text": "Mock synthesis."},
        )

        stage = "verify"
        await events.transition(run_id, "verifying")
        await events.append(run_id, "verify", {"checked": 1, "unsupported": 0, "fixed": 0})
        await asyncio.sleep(1)

        await store.update_run(run_id, summary="mock summary")
        await events.transition(run_id, "done")
        await events.append(
            run_id,
            "done",
            {
                "report_path": None,
                "sources": 0,
                "duration_s": round(time.monotonic() - started, 1),
                "cost_usd": 0.0,
                "tokens": {"prompt": 0, "completion": 0},
            },
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