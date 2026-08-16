# Indus Agent — Toolbox (FastMCP server)

## Overview
The single MCP tool server for Indus Agent (locked decision #5: ONE toolbox). Built on
FastMCP with the **streamable HTTP transport** at `/mcp` (internal port `9000`), started by
compose before the api so tools are available at every boot. Container image:
`python:3.12-slim` non-root, `HEALTHCHECK` on `/health`.

## Stack
- fastmcp~=3.0, httpx, beautifulsoup4, asyncpg (reads `db` directly for RAG),
  trafilatura (page extraction), Ollama `nomic-embed-text` for embeddings (host.docker.internal).

## Layout
```
mcp_servers/toolbox/
  Dockerfile
  requirements.txt
  server.py          — FastMCP("chat-toolbox") app, tool registration, streamable-http :9000
  healthcheck.py     — /health probe (used by healthcheck)
  tools/
    web_search.py    — web.search (SearXNG JSON, self-hosted)
    web_fetch.py     — web.fetch (trafilatura extraction + web_cache TTL + canonical URL)
    arxiv_search.py  — arxiv.search (default disabled; toggleable)
    util_datetime.py — util.datetime (always call before "latest/today")
    rag.py           — rag.search / rag.ingest (pgvector via asyncpg)
    db_pool.py       — shared asyncpg pool
```

## Tool naming
MCP tools use dotted namespaces: `web.search`, `web.fetch`, `util.datetime`, `rag.search`,
`rag.ingest`, `arxiv.search`. The API sanitizes names to `^[a-zA-Z0-9_-]{1,64}$` before
building OpenAI-compat schemas (HP-012).

## RAG integration
`rag.search` / `rag.ingest` read/write the **same** `documents`/`chunks` tables as the API
(shared `DATABASE_URL`, same embedding model `nomic-embed-text`, 768-dim). Results include
`doc`, `chunk_id`, `snippet`, `score`.

## Run / debug
```bash
docker compose up -d toolbox                      # via compose
docker compose exec toolbox python server.py      # manual start
# MCP Inspector (dev only, compose.dev.yaml publishes :9000):
#   uvx mcp-inspector -- http://localhost:9000/mcp
```
> Gotcha (HP-025): after rebuilding/recreating the toolbox container, restart the
> `api` container (`docker compose restart api`) so the MCP session reconnects cleanly.