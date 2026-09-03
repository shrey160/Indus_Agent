"""Event append + run status FSM transitions (one transaction each).

A run's progress is an append-only event log in `research_events`; SSE is a
replayable view over it (PHASE_9 "Event sourcing"). Every status transition is a
single transaction: it appends a `status` event AND updates `research_runs` so
the db row and the log can never disagree.
"""

import json

import db

STATUSES = {"queued", "planning", "researching", "writing", "verifying",
            "done", "failed", "cancelled", "interrupted"}
TERMINAL = {"done", "failed", "cancelled", "interrupted"}

# Purge the run's ephemeral input docs (Phase 10) on these transitions only.
# `interrupted` is TERMINAL for the FSM/SSE but MUST keep docs so resume works.
DOCS_PURGE_STATUSES = frozenset({"done", "failed", "cancelled"})

# FSM (PHASE_9 "Runner Internals"). `interrupted` is only entered via boot
# recovery (force_status), and `interrupted/failed -> queued` only via resume.
TRANSITIONS: dict[str, set[str]] = {
    "queued": {"planning", "cancelled"},
    "planning": {"researching", "failed", "cancelled"},
    "researching": {"writing", "failed", "cancelled"},
    "writing": {"verifying", "failed", "cancelled"},
    "verifying": {"done", "failed", "cancelled"},
    "interrupted": {"queued"},
    "failed": {"queued"},
}


async def append(run_id: str, kind: str, payload: dict) -> int:
    """Append an event to the run log; returns its id."""
    return await db.fetchval(
        """
        INSERT INTO research_events (run_id, kind, payload)
        VALUES ($1, $2, $3)
        RETURNING id
        """,
        run_id,
        kind,
        json.dumps(payload),
    )


async def transition(run_id: str, to_status: str, detail: str | None = None) -> int:
    """Move a run to `to_status`, atomically appending a status event.

    Raises ValueError on an illegal FSM transition.
    """
    if to_status not in STATUSES:
        raise ValueError(f"unknown status {to_status!r}")
    pool = db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            cur = await conn.fetchval(
                "SELECT status FROM research_runs WHERE id = $1 FOR UPDATE", run_id
            )
            if cur is None:
                raise ValueError(f"unknown run {run_id}")
            if to_status not in TRANSITIONS.get(cur, set()):
                raise ValueError(f"illegal transition {cur} -> {to_status}")
            event_id = await conn.fetchval(
                """
                INSERT INTO research_events (run_id, kind, payload)
                VALUES ($1, 'status', $2)
                RETURNING id
                """,
                run_id,
                json.dumps({"status": to_status, "detail": detail}),
            )
            if to_status in TERMINAL:
                if to_status in DOCS_PURGE_STATUSES:
                    await conn.execute(
                        "DELETE FROM research_run_docs WHERE run_id = $1", run_id
                    )
                await conn.execute(
                    """
                    UPDATE research_runs
                    SET status = $1, updated_at = now(), finished_at = now()
                    WHERE id = $2
                    """,
                    to_status,
                    run_id,
                )
            else:
                await conn.execute(
                    """
                    UPDATE research_runs SET status = $1, updated_at = now()
                    WHERE id = $2
                    """,
                    to_status,
                    run_id,
                )
            return event_id


async def force_status(run_id: str, to_status: str, detail: str | None = None) -> int:
    """Set a run's status WITHOUT FSM validation + append a status event.

    Used only by boot recovery (marks non-terminal runs `interrupted`), which is
    allowed from every non-terminal state.
    """
    pool = db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            exists = await conn.fetchval(
                "SELECT 1 FROM research_runs WHERE id = $1", run_id
            )
            if not exists:
                raise ValueError(f"unknown run {run_id}")
            event_id = await conn.fetchval(
                """
                INSERT INTO research_events (run_id, kind, payload)
                VALUES ($1, 'status', $2)
                RETURNING id
                """,
                run_id,
                json.dumps({"status": to_status, "detail": detail}),
            )
            if to_status in TERMINAL:
                if to_status in DOCS_PURGE_STATUSES:
                    await conn.execute(
                        "DELETE FROM research_run_docs WHERE run_id = $1", run_id
                    )
                await conn.execute(
                    """
                    UPDATE research_runs
                    SET status = $1, updated_at = now(), finished_at = now()
                    WHERE id = $2
                    """,
                    to_status,
                    run_id,
                )
            else:
                await conn.execute(
                    """
                    UPDATE research_runs SET status = $1, updated_at = now()
                    WHERE id = $2
                    """,
                    to_status,
                    run_id,
                )
            return event_id


async def events_after(run_id: str, last_id: int) -> list[dict]:
    rows = await db.fetch(
        """
        SELECT id, ts, kind, payload
        FROM research_events
        WHERE run_id = $1 AND id > $2
        ORDER BY id
        """,
        run_id,
        last_id,
    )
    out = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        out.append({"id": r["id"], "ts": r["ts"], "kind": r["kind"], "payload": payload})
    return out
