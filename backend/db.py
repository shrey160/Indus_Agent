import asyncio
import os

import asyncpg

DATABASE_URL = os.environ["DATABASE_URL"]


def _encode_vec(values):
    if values is None:
        return None
    return "[" + ",".join(str(float(x)) for x in values) + "]"


def _decode_vec(text):
    if not text:
        return None
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [float(x) for x in inner.split(",")]
    return None


async def _init_conn(conn: asyncpg.Connection) -> None:
    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await conn.set_type_codec(
        "vector",
        encoder=_encode_vec,
        decoder=_decode_vec,
        schema="public",
        format="text",
    )


DDL: list[str] = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    """
    CREATE TABLE IF NOT EXISTS providers (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        base_url TEXT NOT NULL,
        type TEXT NOT NULL DEFAULT 'openai',
        is_default BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_state (
        id BOOLEAN PRIMARY KEY DEFAULT TRUE,
        active_provider_id INT REFERENCES providers(id) ON DELETE SET NULL,
        active_model TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id SERIAL PRIMARY KEY,
        title TEXT DEFAULT 'New chat',
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        id SERIAL PRIMARY KEY,
        conversation_id INT REFERENCES conversations(id) ON DELETE CASCADE,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        model TEXT,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    "ALTER TABLE providers ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'local'",
    "ALTER TABLE providers ADD COLUMN IF NOT EXISTS preset TEXT",
    "ALTER TABLE providers ADD COLUMN IF NOT EXISTS api_key_enc BYTEA",
    "ALTER TABLE providers ADD COLUMN IF NOT EXISTS key_hint TEXT",
    "ALTER TABLE messages ADD COLUMN IF NOT EXISTS cost_usd NUMERIC(10,6)",
    "ALTER TABLE messages ADD COLUMN IF NOT EXISTS sources JSONB",
    "ALTER TABLE messages ADD COLUMN IF NOT EXISTS tool_events JSONB",
    "ALTER TABLE messages ADD COLUMN IF NOT EXISTS reasoning TEXT",
    """
    CREATE TABLE IF NOT EXISTS provider_favorites (
        provider_id INT REFERENCES providers(id) ON DELETE CASCADE,
        model_id TEXT NOT NULL,
        pinned_at TIMESTAMPTZ DEFAULT now(),
        PRIMARY KEY (provider_id, model_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memories (
        id SERIAL PRIMARY KEY,
        fact TEXT NOT NULL,
        category TEXT DEFAULT 'general',
        embedding vector(768),
        embed_model TEXT,
        confidence REAL DEFAULT 1.0,
        source_msg_id INT REFERENCES messages(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_settings (
        tool_name TEXT PRIMARY KEY,
        enabled BOOLEAN DEFAULT TRUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS documents (
        id SERIAL PRIMARY KEY,
        filename TEXT NOT NULL,
        path TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        error TEXT,
        chunk_count INT DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS chunks (
        id BIGSERIAL PRIMARY KEY,
        document_id INT REFERENCES documents(id) ON DELETE CASCADE,
        idx INT NOT NULL,
        content TEXT NOT NULL,
        embedding vector(768),
        embed_model TEXT NOT NULL,
        UNIQUE(document_id, idx)
    )
    """,
    "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS page INT",
    "ALTER TABLE app_state ADD COLUMN IF NOT EXISTS rag_auto BOOLEAN NOT NULL DEFAULT TRUE",
    "ALTER TABLE memories ADD COLUMN IF NOT EXISTS edited BOOLEAN DEFAULT FALSE",
    "ALTER TABLE app_state ADD COLUMN IF NOT EXISTS retention_months INT",
    """
    CREATE TABLE IF NOT EXISTS web_cache (
        url        TEXT PRIMARY KEY,
        content    TEXT NOT NULL,
        title      TEXT,
        fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        meta       JSONB NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research_runs (
      id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      conversation_id INT REFERENCES conversations(id) ON DELETE SET NULL,
      query           TEXT NOT NULL,
      depth           TEXT NOT NULL DEFAULT 'standard',      -- quick | standard | deep
      status          TEXT NOT NULL DEFAULT 'queued',
        -- queued | planning | researching | writing | verifying
        -- done | failed | cancelled | interrupted
      model_policy    TEXT NOT NULL DEFAULT 'local_only',    -- local_only | allow_cloud
      provider_id     INT,         -- smart-role provider snapshot (providers.id is INT)
      model           TEXT,        -- smart-role model snapshot
      config          JSONB NOT NULL DEFAULT '{}',           -- resolved preset + overrides + role_models hook
      plan            JSONB,
      title           TEXT,
      report_path     TEXT,        -- e.g. research/2026-08/slug-a1b2.md (under /data)
      summary         TEXT,        -- first ~500 chars for list views
      metrics         JSONB NOT NULL DEFAULT '{}',
      error           TEXT,
      created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
      updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
      finished_at     TIMESTAMPTZ
    )
    """,
    "CREATE INDEX IF NOT EXISTS research_runs_status ON research_runs(status, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS research_tasks (
      id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      run_id     UUID NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
      idx        INT NOT NULL,
      question   TEXT NOT NULL,
      kind       TEXT NOT NULL DEFAULT 'research',
      status     TEXT NOT NULL DEFAULT 'pending',  -- pending | running | done | failed | skipped
      summary    TEXT,
      iterations INT NOT NULL DEFAULT 0,
      UNIQUE(run_id, idx)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research_sources (
      id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      run_id       UUID NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
      n            INT NOT NULL,                     -- citation number within the run
      url          TEXT NOT NULL,
      title        TEXT, domain TEXT,
      published_at TIMESTAMPTZ,
      fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
      excerpt      TEXT,                             -- first ~600 chars of extracted text
      fetch_status TEXT NOT NULL DEFAULT 'ok',       -- ok | failed | cached
      meta         JSONB NOT NULL DEFAULT '{}',
      UNIQUE(run_id, n), UNIQUE(run_id, url)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research_notes (
      id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      run_id     UUID NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
      task_id    UUID NOT NULL REFERENCES research_tasks(id) ON DELETE CASCADE,
      source_id  UUID REFERENCES research_sources(id) ON DELETE SET NULL,
      note       TEXT NOT NULL,
      salience   REAL NOT NULL DEFAULT 0.5,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS research_notes_task ON research_notes(task_id, salience DESC)",
    """
    CREATE TABLE IF NOT EXISTS research_events (
      id      BIGSERIAL PRIMARY KEY,
      run_id  UUID NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
      ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
      kind    TEXT NOT NULL,
      payload JSONB NOT NULL DEFAULT '{}'
    )
    """,
    "CREATE INDEX IF NOT EXISTS research_events_run ON research_events(run_id, id)",
]

DEFAULT_PROVIDERS = [
    ("Ollama", "http://host.docker.internal:11434", "ollama"),
    ("LM Studio", "http://host.docker.internal:1234", "openai"),
]

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    last_exc: Exception | None = None
    for _ in range(10):
        try:
            _pool = await asyncpg.create_pool(DATABASE_URL, init=_init_conn)
            return _pool
        except Exception as exc:
            last_exc = exc
            await asyncio.sleep(1)
    raise RuntimeError(f"could not connect to database: {last_exc}")


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database pool not initialized")
    return _pool


async def run_ddl() -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        for statement in DDL:
            await conn.execute(statement)


async def seed() -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        for name, base_url, ptype in DEFAULT_PROVIDERS:
            await conn.execute(
                """
                INSERT INTO providers (name, base_url, type, is_default)
                SELECT $1, $2, $3, TRUE
                WHERE NOT EXISTS (SELECT 1 FROM providers WHERE name = $1)
                """,
                name,
                base_url,
                ptype,
            )
        await conn.execute(
            "INSERT INTO app_state (id) VALUES (TRUE) ON CONFLICT (id) DO NOTHING"
        )


async def fetch(query: str, *args):
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(query, *args)


async def fetchrow(query: str, *args):
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(query, *args)


async def fetchval(query: str, *args):
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(query, *args)


async def execute(query: str, *args):
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(query, *args)


async def check() -> bool:
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception:
        return False
