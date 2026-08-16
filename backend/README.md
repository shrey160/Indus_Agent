# Indus Agent — Backend (FastAPI)

## Overview
The application core of Indus Agent (Local AI Hub). A Python 3.12 FastAPI service (`:8000`) that
wraps your local/cloud LLM providers, streams chat over SSE, persists conversations and facts in
Postgres (pgvector), retrieves memory, runs an agent tool loop against the MCP toolbox, handles
document RAG, and executes deep-research pipelines. Runs non-root inside Docker.

## Stack
- FastAPI + uvicorn, `asyncpg` (async Postgres), pydantic v2, httpx, `mcp` streamable-HTTP client.
- Database schema bootstrap is **idempotent startup DDL only** (`CREATE ... IF NOT EXISTS`,
  `ALTER ... ADD COLUMN IF NOT EXISTS`) — additive-only, never rename/drop (locked #12).
- LLM calls always use the **OpenAI-compatible** wire protocol (`/v1/chat/completions`) (locked #1).

## Layout
```
backend/
  main.py              — app factory, lifespan (pool, DDL, boot recovery), routers
  db.py                — asyncpg pool + `DDL` schema bootstrap list
  providers/           — provider registry + drivers (Ollama, OpenAI-compat, cloud presets)
    base.py            — ProviderBase ABC, shared streaming/usage aggregation
    ollama.py          — Ollama driver (host.docker.internal:11434)
    openai_compat.py   — LM Studio / Unsloth / any OpenAI-compatible endpoint
    cloud.py           — OpenRouter/OpenAI/Groq/Together presets
    presets.py         — CLOUD_PRESETS dict
    registry.py        — detection, cache, activation, build_provider (decrypts keys)
    crypto.py          — Fernet key encryption/decryption
    role_models.py     — local fast/smart role picker (filters :cloud stubs, ASR, embeds)
    router.py          — /api/providers* HTTP endpoints
  chat/                — SSE streaming chat + context assembly
    router.py        — /api/chat SSE, conversation/message persistence
    context.py       — persona + memory + RAG context injection, token budget
    titles.py        — auto-title generation for conversations
  agent/               — tool-use loop
    loop.py          — iteration/tool-call budget, streaming, sources collection
    fallback_parser.py— JSON tool-call fallback for non-native tools
  mcp_client/          — MCP client (streamable HTTP)
    manager.py       — discovery, session lifecycle, reconnect
  tools_registry.py    — MCP tool registry (enabled/healthy gating, KNOWN_TOOLS fallback)
  rag/                 — document upload → chunk → embed → pgvector
  memory/              — soul file, fact extraction, budgeted memory injection
  research/            — deep-research runner (planner/researcher/writer/verifier)
  retention.py         — archive old conversations
  backup.py            — export/import tar.gz (db dump + /data)
  requirements.txt     — pinned deps
  Dockerfile           — python:3.12-slim, postgresql-client-16 (trixie-pgdg), non-root
```

## Key behaviors
- **Absolute imports only** (`import db`, `from providers import ...`) — relative imports break
  inside the container (see HP-001).
- SSE streaming: chat endpoints emit `event: <type>` lines (`content`, `reasoning`, `usage`,
  `tool`, `tool_limit`, `final`, ...). The frontend proxy pipes them unbuffered.
- `agent/loop.py` constants: `MAX_TOOL_ITERATIONS=5`, `MAX_TOOL_CALLS=15`, `TOOL_TIMEOUT_S=10.0`.
- Tools go through MCP (`mcp_client.manager`, streamable HTTP at `toolbox:9000/mcp`).
- Secrets are never logged; `Authorization` headers never logged.

## Endpoints
See the root `README.md` → "API reference" table for the full list. Short summary:
```
GET  /health                      — Docker HEALTHCHECK probe
GET  /api/info                    — app/db info
GET  /api/providers*              — providers, detection, models, activate, key
POST /api/chat                    — SSE chat stream {message, conversation_id?}
GET  /api/conversations           — list (LIMIT 200)
GET  /api/conversations/{id}/messages
POST /api/documents               — upload (multipart), GET list, GET/DELETE/{id}, POST /{id}/reingest
GET  /api/memories                — fact memory (q=, category=)
GET/PUT /api/soul                 — persona file
GET  /api/tools / /api/tools/{name}/toggle|test   — MCP tools
GET  /api/settings/retention ─ PUT — retention control (or null)
GET  /api/export / POST /api/import — backup/restore (tar.gz)
GET  /api/research*               — deep-research runs, SSE stream, cancel/resume/report
```

## Dev
```bash
docker compose -f compose.yaml -f compose.dev.yaml up --build -d   # live reload via bind mount
docker compose exec api python -m ...                              # in-container python
```
Apply test scripts via `docker cp` + `docker compose exec -T api python /tmp/script.py` (HP-029).