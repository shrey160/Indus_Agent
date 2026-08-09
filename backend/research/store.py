"""All SQL for research runs/tasks/sources/notes/events.

Style follows rag/retriever.py: module constants at top, docstrings on public
functions, db.fetch* helpers for reads and an acquired connection for writes
that need a transaction. Uses absolute imports only (HP-001).
"""

import json
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import db


def canonicalize_url(url: str) -> str:
    """Canonical URL used as the web_cache primary key.

    MUST match toolbox copy in mcp_servers/toolbox/tools/web_fetch.py
    (PHASE_8 §3 -> Phase 9 handoff). Byte-for-byte copy.
    """
    p = urlsplit(url.strip())
    scheme = (p.scheme or "https").lower()
    host = (p.hostname or "").lower()
    netloc = host if p.port in (None, 80, 443) else f"{host}:{p.port}"
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    pairs = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")
    ]
    return urlunsplit((scheme, netloc, path, urlencode(pairs), ""))


def _row_dict(row) -> dict | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def _db_text(value: str | None) -> str | None:
    """Postgres cannot store NUL bytes (0x00) in text; web-derived content
    occasionally contains them (malformed HTML in fetched pages). Strip
    before INSERT — same sanitization at every web-text boundary."""
    if value is None:
        return None
    return value.replace("\x00", "")


async def create_run(
    query: str,
    depth: str,
    model_policy: str,
    conversation_id: int | None,
    config: dict,
) -> dict:
    """Insert a queued run and return its row as a dict."""
    row = await db.fetchrow(
        """
        INSERT INTO research_runs (query, depth, model_policy, conversation_id, config)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING *
        """,
        query,
        depth,
        model_policy,
        conversation_id,
        json.dumps(config),
    )
    return _row_dict(row)


async def get_run(run_id: str) -> dict | None:
    row = await db.fetchrow("SELECT * FROM research_runs WHERE id = $1", run_id)
    return _row_dict(row)


async def list_runs(status: str | None = None, limit: int = 50) -> list[dict]:
    if status:
        rows = await db.fetch(
            """
            SELECT * FROM research_runs
            WHERE status = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            status,
            limit,
        )
    else:
        rows = await db.fetch(
            "SELECT * FROM research_runs ORDER BY created_at DESC LIMIT $1", limit
        )
    return [_row_dict(r) for r in rows]


_UPDATE_WHITELIST = {
    "plan",
    "title",
    "report_path",
    "summary",
    "metrics",
    "error",
    "provider_id",
    "model",
    "finished_at",
}
_JSON_FIELDS = {"plan", "metrics"}


async def update_run(run_id: str, **fields) -> None:
    """Update whitelisted run fields; always bumps updated_at."""
    sets = []
    args = []
    for key, value in fields.items():
        if key not in _UPDATE_WHITELIST:
            raise ValueError(f"unknown research_runs field {key!r}")
        sets.append(f"{key} = ${len(args) + 1}")
        args.append(json.dumps(value) if key in _JSON_FIELDS else value)
    sets.append("updated_at = now()")
    args.append(run_id)
    await db.execute(
        f"UPDATE research_runs SET {', '.join(sets)} WHERE id = ${len(args)}",
        *args,
    )


async def insert_tasks(run_id: str, tasks: list[dict]) -> None:
    """Replace the run's task set (one transaction).

    Resume re-runs the PLAN stage, so the previous attempt's tasks are
    replaced wholesale; a fresh insert would collide on UNIQUE(run_id, idx).
    Notes cascade via the task_id FK; sources keep their citation numbers
    (add_source dedups by canonical URL).
    """
    pool = db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM research_tasks WHERE run_id = $1", run_id)
            for task in tasks:
                await conn.execute(
                    """
                    INSERT INTO research_tasks (run_id, idx, question, kind, status)
                    VALUES ($1, $2, $3, $4, 'pending')
                    """,
                    run_id,
                    task["idx"],
                    task["question"],
                    task.get("kind", "research"),
                )


async def get_tasks(run_id: str) -> list[dict]:
    rows = await db.fetch(
        "SELECT * FROM research_tasks WHERE run_id = $1 ORDER BY idx", run_id
    )
    return [_row_dict(r) for r in rows]


async def set_task(
    task_id: str,
    status: str,
    summary: str | None = None,
    iterations: int | None = None,
) -> None:
    pool = db.get_pool()
    async with pool.acquire() as conn:
        sets = ["status = $2"]
        args = [task_id, status]
        if summary is not None:
            args.append(summary)
            sets.append(f"summary = ${len(args)}")
        if iterations is not None:
            args.append(iterations)
            sets.append(f"iterations = ${len(args)}")
        await conn.execute(
            f"UPDATE research_tasks SET {', '.join(sets)} WHERE id = $1", *args
        )


async def add_source(
    run_id: str,
    url: str,
    title: str | None,
    domain: str | None,
    excerpt: str | None,
    fetch_status: str,
    meta: dict,
) -> dict:
    """Add a source, assigning the next citation number `n` within the run.

    Duplicate canonical URL (same run) is deduped via ON CONFLICT DO UPDATE —
    the existing row's citation number is preserved and returned.
    """
    canonical = canonicalize_url(url)
    pool = db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            next_n = await conn.fetchval(
                "SELECT COALESCE(MAX(n), 0) + 1 FROM research_sources WHERE run_id = $1",
                run_id,
            )
            row = await conn.fetchrow(
                """
                INSERT INTO research_sources
                    (run_id, n, url, title, domain, excerpt, fetch_status, meta)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (run_id, url) DO UPDATE SET
                    fetch_status = EXCLUDED.fetch_status,
                    excerpt = COALESCE(EXCLUDED.excerpt, research_sources.excerpt)
                RETURNING *
                """,
                run_id,
                next_n,
                canonical,
                _db_text(title),
                _db_text(domain),
                _db_text(excerpt),
                fetch_status,
                json.dumps(meta),
            )
            return _row_dict(row)


async def add_note(
    run_id: str,
    task_id: str,
    source_id: str | None,
    note: str,
    salience: float,
) -> None:
    await db.execute(
        """
        INSERT INTO research_notes (run_id, task_id, source_id, note, salience)
        VALUES ($1, $2, $3, $4, $5)
        """,
        run_id,
        task_id,
        source_id,
        _db_text(note),
        float(salience),
    )


async def get_task_notes(task_id: str) -> list[dict]:
    rows = await db.fetch(
        "SELECT * FROM research_notes WHERE task_id = $1 ORDER BY salience DESC",
        task_id,
    )
    return [_row_dict(r) for r in rows]


async def get_run_sources(run_id: str) -> list[dict]:
    rows = await db.fetch(
        "SELECT * FROM research_sources WHERE run_id = $1 ORDER BY n ASC", run_id
    )
    return [_row_dict(r) for r in rows]


async def seen_urls(run_id: str) -> set[str]:
    rows = await db.fetch(
        "SELECT url FROM research_sources WHERE run_id = $1", run_id
    )
    return {r["url"] for r in rows}


async def fresh_cache_urls(ttl_hours: int) -> set[str]:
    rows = await db.fetch(
        """
        SELECT url FROM web_cache
        WHERE fetched_at > now() - make_interval(hours => $1)
        """,
        ttl_hours,
    )
    return {r["url"] for r in rows}


async def run_counts(run_id: str) -> dict:
    row = await db.fetchrow(
        """
        SELECT
          (SELECT count(*) FROM research_tasks WHERE run_id = $1) AS tasks,
          (SELECT count(*) FROM research_tasks WHERE run_id = $1 AND status = 'done') AS tasks_done,
          (SELECT count(*) FROM research_sources WHERE run_id = $1) AS sources,
          (SELECT count(*) FROM research_notes WHERE run_id = $1) AS notes
        """,
        run_id,
    )
    return _row_dict(row)
