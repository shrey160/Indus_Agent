"""Research REST endpoints (start, list, detail, sources, report, delete).

The SSE stream lives here too (SP-2 T3). Runs are JOBS: a client disconnect on
the stream must NEVER cancel the run (deliberate inversion of the chat HP-004
rule — PHASE_9 "SSE Contract"). Cancel is an explicit POST.
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

import db
from backup import is_restoring
from research import config, events, runner, store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/research", tags=["research"])

# Mirror of main.DATA_DIR: importing `main` here would be circular (main imports
# this router). Same env var, same process — keep them in lockstep by hand.
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))


class StartRequest(BaseModel):
    query: str
    depth: str = "standard"
    model_policy: str = "local_only"
    conversation_id: int | None = None
    config_overrides: dict | None = None


def _run_dict(run: dict) -> dict:
    """Decode the jsonb columns asyncpg returns as strings."""
    run = dict(run)
    for key in ("config", "plan", "metrics"):
        value = run.get(key)
        if isinstance(value, str):
            try:
                run[key] = json.loads(value)
            except json.JSONDecodeError:
                run[key] = None
    return run


async def _get_run_or_404(run_id: str) -> dict:
    run = await store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.post("", status_code=201)
async def start_run(body: StartRequest) -> dict:
    if is_restoring():
        raise HTTPException(503, "restoring backup")
    query = body.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    if len(query) > 4000:
        raise HTTPException(status_code=400, detail="query too long (max 4000 chars)")
    if body.depth not in config.PRESETS:
        raise HTTPException(status_code=400, detail=f"unknown depth {body.depth!r}")
    if body.model_policy not in {"local_only", "allow_cloud"}:
        raise HTTPException(
            status_code=400, detail="model_policy must be 'local_only' or 'allow_cloud'"
        )
    if body.conversation_id is not None:
        exists = await db.fetchval(
            "SELECT 1 FROM conversations WHERE id = $1", body.conversation_id
        )
        if not exists:
            raise HTTPException(
                status_code=400, detail="conversation_id does not exist"
            )
    try:
        resolved = config.resolve_config(body.depth, body.config_overrides)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    run = await store.create_run(
        query, body.depth, body.model_policy, body.conversation_id, resolved
    )
    if body.conversation_id is not None:
        # Chat integration (PHASE_9): one-line system notice via the normal chat
        # path. build_messages filters system rows out of the prompt.
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES ($1, 'system', $2)",
            body.conversation_id,
            f"RESEARCH STARTED ▸ {query}",
        )
    return {"run_id": str(run["id"]), "status": "queued"}


@router.get("")
async def list_runs(status: str | None = None, limit: int = 50) -> list[dict]:
    runs = await store.list_runs(status=status, limit=limit)
    out = []
    for run in runs:
        counts = await store.run_counts(str(run["id"]))
        out.append(
            {
                "id": str(run["id"]),
                "query": run["query"],
                "depth": run["depth"],
                "status": run["status"],
                "title": run["title"],
                "model": run["model"],
                "counts": counts,
                "created_at": run["created_at"],
                "finished_at": run["finished_at"],
            }
        )
    return out


@router.get("/{run_id}")
async def run_detail(run_id: str) -> dict:
    run = await _get_run_or_404(run_id)
    detail = _run_dict(run)
    detail["id"] = str(run["id"])
    detail["tasks"] = await store.get_tasks(run_id)
    detail["counts"] = await store.run_counts(run_id)
    return detail


@router.get("/{run_id}/stream")
async def run_stream(run_id: str, last_event_id: int = 0):
    await _get_run_or_404(run_id)

    async def gen():
        last = last_event_id
        while True:
            rows = await events.events_after(run_id, last)
            for row in rows:
                last = row["id"]
                yield (
                    f"id: {row['id']}\n"
                    f"data: {json.dumps({row['kind']: row['payload'], 'ts': row['ts'].isoformat()})}\n\n"
                )
            run = await store.get_run(run_id)
            if not rows and run is not None and run["status"] in events.TERMINAL:
                break
            await asyncio.sleep(0.5)

    # Disconnect does NOT cancel the run (PHASE_9 — deliberate inversion of the
    # chat HP-004 rule): the job is decoupled from the connection. Do NOT hook
    # request disconnect to cancellation; cancel is an explicit POST /cancel.
    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{run_id}/sources")
async def run_sources(run_id: str) -> list[dict]:
    await _get_run_or_404(run_id)
    return await store.get_run_sources(run_id)


@router.get("/{run_id}/report")
async def run_report(run_id: str):
    run = await _get_run_or_404(run_id)
    report_path = run["report_path"]
    if not report_path:
        raise HTTPException(status_code=404, detail="no report for this run")
    if ".." in report_path:
        raise HTTPException(status_code=400, detail="invalid report path")
    path = Path(DATA_DIR) / report_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="report file missing")
    return PlainTextResponse(
        path.read_text(encoding="utf-8"), media_type="text/markdown"
    )


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict:
    await _get_run_or_404(run_id)
    try:
        status = await runner.request_cancel(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"run_id": run_id, "status": status}


@router.post("/{run_id}/resume")
async def resume_run(run_id: str) -> dict:
    await _get_run_or_404(run_id)
    try:
        await runner.resume_run(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"run_id": run_id, "status": "queued"}


@router.delete("/{run_id}")
async def delete_run(run_id: str) -> dict:
    run = await _get_run_or_404(run_id)
    if run["status"] not in events.TERMINAL:
        raise HTTPException(
            status_code=409, detail=f"run is {run['status']} — cannot delete"
        )
    if run["report_path"]:
        path = Path(DATA_DIR) / run["report_path"]
        path.unlink(missing_ok=True)
    await db.execute("DELETE FROM research_runs WHERE id = $1", run_id)
    return {"ok": True}