import logging
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup
from trafilatura import extract as trafilatura_extract

from tools.db_pool import get_pool

MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MB

logger = logging.getLogger(__name__)

_table_ensured = False


def canonicalize_url(url: str) -> str:
    """Canonical URL used as the web_cache primary key.

    MUST match api-side copy in Phase 9 research/store.py.
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


async def _ensure_table() -> None:
    global _table_ensured
    if _table_ensured:
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS web_cache (
                    url        TEXT PRIMARY KEY,
                    content    TEXT NOT NULL,
                    title      TEXT,
                    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    meta       JSONB NOT NULL DEFAULT '{}'
                )
                """
            )
        _table_ensured = True
    except Exception as exc:
        logger.warning("web_cache ensure_table failed: %s", exc)


async def _try_cache(url: str, ttl_hours: int) -> dict | None:
    if ttl_hours <= 0:
        return None
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT content, title, fetched_at
                FROM web_cache
                WHERE url = $1
                  AND fetched_at > now() - make_interval(hours => $2)
                """,
                url,
                ttl_hours,
            )
        if row:
            return {
                "content": row["content"],
                "title": row["title"] or "",
                "fetched_at": row["fetched_at"].isoformat(),
            }
    except Exception as exc:
        logger.warning("web_cache read failed for %s: %s", url, exc)
    return None


async def _upsert_cache(url: str, content: str, title: str | None) -> None:
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO web_cache (url, content, title, meta)
                VALUES ($1, $2, $3, '{}')
                ON CONFLICT (url) DO UPDATE
                SET content = EXCLUDED.content,
                    title = EXCLUDED.title,
                    fetched_at = now()
                """,
                url,
                content,
                title,
            )
    except Exception as exc:
        logger.warning("web_cache upsert failed for %s: %s", url, exc)


def _extract_raw(body: str) -> tuple[str, str]:
    soup = BeautifulSoup(body, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned = "\n".join(lines)
    return cleaned, title


def _extract_auto(body: str) -> tuple[str, str]:
    text = trafilatura_extract(
        body,
        include_comments=False,
        include_tables=False,
    )
    if text:
        return text, ""
    # Fallback to legacy BeautifulSoup strip if trafilatura returns nothing.
    return _extract_raw(body)


async def web_fetch(
    url: str,
    max_chars: int = 8000,
    extract: str = "auto",
    cache_ttl_hours: int = 24,
) -> dict:
    """Fetch a URL and return cleaned readable text.

    extract: "auto" uses trafilatura main-content extraction (falls back to BS4);
             "raw" uses the legacy BeautifulSoup strip.
    cache_ttl_hours: 0 disables cache reads, but a live fetch is still upserted.
    Returns: {url, title, text, truncated, chars, cached, fetched_at, source}
    """
    await _ensure_table()
    canonical = canonicalize_url(url)

    cached = None
    if cache_ttl_hours > 0:
        cached = await _try_cache(canonical, cache_ttl_hours)

    if cached is not None:
        text = cached["content"][:max_chars]
        return {
            "url": canonical,
            "title": cached["title"],
            "text": text,
            "truncated": len(cached["content"]) > max_chars,
            "chars": len(text),
            "cached": True,
            "fetched_at": cached["fetched_at"],
            "source": url,
        }

    async with httpx.AsyncClient(
        timeout=10.0, follow_redirects=True, headers={"User-Agent": "Local-AI-Hub/1.0"}
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        body = response.text[:MAX_BODY_BYTES]
        final_url = str(response.url)

    canonical = canonicalize_url(final_url)

    if extract == "raw":
        cleaned, title = _extract_raw(body)
    else:
        cleaned, title = _extract_auto(body)

    await _upsert_cache(canonical, cleaned, title or None)

    text = cleaned[:max_chars]
    return {
        "url": canonical,
        "title": title,
        "text": text,
        "truncated": len(cleaned) > max_chars,
        "chars": len(text),
        "cached": False,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "source": url,
    }
