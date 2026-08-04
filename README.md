# Indus Agent

A self-hosted, local-first AI chat hub, fully Dockerized. It auto-detects local LLM servers on your machine (Ollama / LM Studio), streams chat over SSE, and persists your history in Postgres. Everything runs on your hardware — nothing leaves the machine unless you connect a cloud provider.

> Working name in the UI/code: **Local AI Hub**. All LLM traffic uses the OpenAI-compatible wire protocol, so the API speaks one language regardless of the underlying provider.

## Features

- **Local provider detection** — auto-discovers Ollama and LM Studio on the host (fast parallel probe, cached); add/remove custom OpenAI-compatible endpoints.
- **Streaming chat** — token-by-token SSE streaming through the Node proxy (visible reasoning stream, stop-generation).
- **Persistent history** — conversations and messages survive restarts in Postgres.
- **Provider management UI** — detect / add / test / activate per provider and model, with live status dots and a searchable model picker.
- **Zero-dependency frontend** — vanilla JS, no build step, Bloomberg-terminal-style UI with a keyboard-driven F-key bar.

## Architecture

```
┌────────────── Docker network: appnet ──────────────┐
│  frontend :3000 ──/api/*──► api :8000 (FastAPI)     │
│  (node:20-alpine,          ├─ provider registry     │
│   zero-dep server.js)      ├─ SSE chat streaming    │
│                            └─ Postgres persistence  │
│                    ┌───────┴────────┐               │
│                    ▼                ▼               │
│              db (pgvector:pg16)   ./data bind mount │
│              pgdata volume        soul.md, docs/    │
└────────────┬────────────────────────────────────────┘
             │
  host.docker.internal
             ▼
     Ollama :11434 / LM Studio :1234 (on your host)
```

| Service | Image | Published port | Purpose |
|---------|-------|:---:|---------|
| `frontend` | `node:20-alpine` (custom) | `127.0.0.1:3000` | Static files + `/api` proxy (unbuffered SSE) |
| `api` | `python:3.12-slim` (custom) | `127.0.0.1:8000` | Application core (FastAPI) |
| `db` | `pgvector/pgvector:pg16` | — (internal) | Relational + vector storage |

Only `3000` and `8000` are published — and bound to `127.0.0.1` (localhost-only).

## Roadmap

- **Phase 2** — Cloud providers (OpenRouter) with Fernet-encrypted API keys, LOCAL/CLOUD sidebar split, searchable model picker.
- **Phase 3** — Persona & memory: `soul.md` injection, async fact extraction, memory with token budget.
- **Phase 4** — MCP toolbox + web tools (FastMCP streamable-HTTP server, self-hosted SearXNG).
- **Phase 5** — RAG: upload → chunk → embed → pgvector; citations.
- **Phase 6** — Management UI & polish: conversations/memory/soul/documents, export/import, hardening.
- **Phase 7** — Advanced tracks (optional): sandbox, routing policies, local media, research feeds.

## Prerequisites

- **Docker Desktop** (Windows/macOS) or Docker Engine + Docker Compose v2 (Linux). `docker compose` v2 is required.
- **Optional** for local models: [Ollama](https://ollama.com) and/or [LM Studio](https://lmstudio.ai) installed **on your host** (not in Docker).
- Free ports `3000` and `8000` on your machine.

## Quickstart

### 1. Clone and configure

```bash
git clone https://github.com/shrey160/Indus_Agent.git
cd Indus_Agent
cp .env.example .env
```

Edit `.env` and fill in real values (never commit `.env`):

```bash
# Postgres password — any strong random string
DB_PASSWORD=changeme
# Fernet key (32-byte, urlsafe base64) — used to encrypt cloud API keys (Phase 2)
SECRET_KEY=<generated below>
```

Generate a Fernet key:

```bash
python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

The `TEST_OPENROUTER_KEY`, `OPENROUTER_ENDPOINT`, and `ALLOWED_MODEL_OPENROUTER` variables are optional and only used by future (Phase 2) testing — you can leave them empty.

### 2. Build and run

```bash
docker compose up --build -d
```

### 3. Verify

```bash
curl localhost:8000/health      # {"status":"ok"}
curl localhost:8000/api/info    # app + db status
curl localhost:3000/            # the UI
```

Then open **http://localhost:3000** in Chrome.

### 4. Stop

```bash
docker compose down             # keeps the pgdata volume
```

Never run `docker compose down -v` unless you intend to wipe the database.

## Host networking (Ollama / LM Studio)

Containers reach LLM servers on the host via `host.docker.internal` (compose maps it automatically). Both Ollama and LM Studio bind to `127.0.0.1` by default, which containers **cannot** reach — you must bind them to `0.0.0.0`:

- **Windows/macOS (Docker Desktop):** `host.docker.internal` works out of the box.
  - Ollama: set the system environment variable `OLLAMA_HOST=0.0.0.0`, then restart Ollama.
  - LM Studio: Server tab → enable "Serve on local network".
- **Linux:** compose already adds `host-gateway`.
  - Ollama: `systemctl edit ollama` → add `[Service]` with `Environment="OLLAMA_HOST=0.0.0.0"` → `systemctl restart ollama`.

> **Warning:** binding `0.0.0.0` exposes the LLM server to your LAN. Restrict with a firewall if unwanted.

## Usage

1. Open **http://localhost:3000**.
2. Press **F5** (or Providers section → Re-detect) to probe Ollama/LM Studio.
3. Select a model in the searchable picker and **Activate** it. The first activation warms the model up — the first reply may be slow while it loads.
4. Type a message in the chat input and send. Tokens stream live; use the stop button to cancel generation.

Keyboard shortcuts: **F1** help, **F2** sidebar, **F4** new chat, **F5** re-detect providers, **F10** log filter, **ESC** dismiss.

## Development (live reload)

```bash
docker compose -f compose.yaml -f compose.dev.yaml up --build -d
```

Python edits trigger uvicorn reload, `server.js` edits restart Node, static files update instantly. Dependency changes (`requirements.txt`, `package.json`) still require `--build`.

## Configuration reference

| Variable | Required | Purpose |
|----------|:---:|---------|
| `DB_PASSWORD` | yes | Postgres password (set in compose's `DATABASE_URL`) |
| `SECRET_KEY` | yes | Fernet key — encrypts cloud API keys at rest (Phase 2) |
| `TEST_OPENROUTER_KEY` | optional | OpenRouter key for Phase 2 acceptance testing |
| `OPENROUTER_ENDPOINT` | optional | Override OpenRouter base URL for testing |
| `ALLOWED_MODEL_OPENROUTER` | optional | Restrict OpenRouter models during testing |

The api service also receives `DATABASE_URL` and `DATA_DIR=/data` from compose (not `.env`).

## API reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Liveness probe |
| `/api/info` | GET | App name, phase, live DB check |
| `/api/providers` | GET | List providers with cached status + models |
| `/api/providers` | POST | Add a provider (`name`, `base_url`, `type` = `ollama`/`openai`) |
| `/api/providers/detect` | POST | Force re-detection of local providers |
| `/api/providers/active` | GET | Currently active provider + model |
| `/api/providers/{id}` | DELETE | Remove a provider (defaults cannot be deleted) |
| `/api/providers/{id}/models` | GET | List models for a provider |
| `/api/providers/{id}/test` | POST | Test a model with a prompt |
| `/api/providers/{id}/activate` | POST | Activate a model (`{ "model": "..." }`), returns warmup info |
| `/api/chat` | POST | SSE stream — `{ "message", "conversation_id"? }` |
| `/api/conversations` | GET / POST | List conversations / create empty one |
| `/api/conversations/{id}/messages` | GET | Message history for a conversation |

## Project structure

```
local-ai-hub/
  compose.yaml              # production
  compose.dev.yaml          # dev overrides (live reload)
  .env / .env.example      # secrets + example (never commit .env)
  .dockerignore
  data/                     # bind-mounted to api at /data
    soul.md                 # assistant persona (auto-created; gitignored)
    soul.example.md         # distributable persona template
  backend/                  # FastAPI app (python:3.12-slim)
    main.py                 # app factory, routers, startup DDL
    db.py                   # asyncpg pool, schema bootstrap
    chat/                   # SSE streaming chat + context assembly
    providers/              # registry, Ollama + OpenAI-compat drivers
  frontend/                 # zero-dep Node static server + proxy (node:20-alpine)
    server.js
    public/                 # index.html, css/, js/
```

## Data & persistence

- **Postgres** — queryable data (providers, conversations, messages) lives in the `pgdata` Docker volume. Backup with `docker compose exec db pg_dump -U localai localai`.
- **`./data`** — human-editable files (e.g. `soul.md`, the assistant persona). It is a bind mount, so it's a plain folder on your host; gitignored content is never committed.

## Security & privacy

- Published ports bind to `127.0.0.1` only — no authentication, single-user localhost app.
- `.env` holds secrets and is gitignored — never commit or share it.
- API keys are encrypted at rest (Phase 2) and never returned to the frontend or logged.
- Cloud provider base URLs must be HTTPS.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Provider shows `down` / `provider unreachable` | Ollama/LM Studio must bind to `0.0.0.0` (see Host networking) and be running. |
| `no_model` on chat | No model activated yet — activate one in the Providers section first. |
| `provider_down` on chat | The activated provider is not responding; check it after re-detect (F5). |
| First reply is very slow | Normal — model is loading into memory. Keep it activated to avoid repeat warmups. |
| Port already in use | Stop whatever occupies `3000`/`8000`, or change the mapping in `compose.yaml`. |
| `docker compose config` fails | `docker compose v2` is required; `.env` must exist with all `${VAR}` references. |
