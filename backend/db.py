import asyncio
import os

import asyncpg

DATABASE_URL = os.environ["DATABASE_URL"]

DDL: list[str] = [
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
            _pool = await asyncpg.create_pool(DATABASE_URL)
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
