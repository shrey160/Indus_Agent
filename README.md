# Indus Agent

A self-hosted, local-first AI chat hub, fully Dockerized. It auto-detects local LLM servers on your machine (Ollama / LM Studio), streams chat over SSE, and persists your history in Postgres. Everything runs on your hardware — nothing leaves the machine unless you connect a cloud provider.

> Working name in the UI/code: **Local AI Hub**. All LLM traffic uses the OpenAI-compatible wire protocol, so the API speaks one language regardless of the underlying provider.

## Features

- **Local provider detection** — auto-discovers Ollama and LM Studio on the host (fast parallel probe, cached); add/remove custom OpenAI-compatible endpoints.
- **Streaming chat** — token-by-token SSE streaming through the Node proxy (visible reasoning stream, stop-generation).
- **Persistent history** — conversations and messages survive restarts in Postgres.
- **Provider management UI** — detect / add / test / activate per provider and model, with live status dots and a searchable model picker.
- **Document RAG** — upload PDF/MD/TXT files; they are chunked, embedded with Ollama `nomic-embed-text`, stored in pgvector, and cited automatically in replies.
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

- **Phase 2** — Cloud providers (OpenRouter) with Fernet-encrypted API keys, LOCAL/CLOUD sidebar split, searchable model picker. *(done)*
- **Phase 3** — Persona & memory: `soul.md` injection, async fact extraction, memory with token budget. *(done)*
- **Phase 4** — MCP toolbox + web tools (FastMCP streamable-HTTP server, self-hosted SearXNG). *(done)*
- **Phase 5** — RAG: upload → chunk → embed → pgvector; citations. *(done)*
- **Phase 6** — Management UI & polish: conversations/memory/soul/documents, export/import, retention, hardening. *(done)*
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

The `TEST_OPENROUTER_KEY`, `OPENROUTER_ENDPOINT`, and `ALLOWED_MODEL_OPENROUTER` variables are optional and only used for Phase 2 acceptance testing — you can leave them empty.

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

Keyboard shortcuts: **F1** help, **F2** sidebar, **F4** new chat, **F5** re-detect providers, **F9** upload documents, **F10** log filter, **F11** export / settings, **Ctrl/Cmd+K** model picker, **ESC** dismiss.

## Chat with your documents (RAG)

Upload PDF, Markdown, or plain-text files and ask questions about them. The app chunks them, embeds them with the host Ollama (`nomic-embed-text`), stores vectors in Postgres, and injects relevant snippets into the system prompt. Replies cite their source; click the `[1]` superscript to see the file name and snippet.

1. Open the **Documents** sidebar section or press **F9** to open the file picker.
2. Drag a file onto the chat area, or click **UPLOAD** and select one or more files (≤ 50 MB, `.pdf` / `.md` / `.txt`).
3. Wait for the status badge to change from `pending` → `processing` → `ready`. The UI polls in the background.
4. Ask a question about the document. When the model uses the context, you will see a cyan `[1]` citation; clicking it opens a popover with the source file and excerpt.

The `[ AUTO ]` toggle controls whether the assistant automatically retrieves context for every message. Even with auto-retrieval off, the model can still call the `rag.search` tool explicitly.

Ad-hoc notes can be indexed without a file via the `rag.ingest` tool in the Tools section (`text` + `title`).

## Deep research

Long-form, citation-backed reports: the app plans sub-questions, searches the web through the SearXNG instance, fetches pages, extracts task-relevant notes, writes a structured markdown report with numbered citations, and verifies it against the sources.

1. Open the **Research** sidebar section (or press **F12**), `[ + ]` → type a research question → pick a preset → optionally pick a specific model for the research (smart) role via the **SMART MODEL ▾** dropdown (default `AUTO (POLICY)`) → `[ START ]`.
2. Track progress live: event log, per-task status, sources, and the report view (rendered `[ RENDER ]` or raw `[ RAW ]`).
3. From a conversation, the `[ RESEARCH ▸ ]` chip above the composer starts a run attached to that conversation — you get `RESEARCH STARTED ▸ …` / `RESEARCH DONE ▸ …` system notices.
4. Reports land in `data/research/YYYY-MM/<slug>-<id8>.md`; `[ DOWNLOAD .md ]` / `[ PRINT ]` in the report view.

Via API:

```bash
curl -X POST http://localhost:8000/api/research \
  -H "Content-Type: application/json" \
  -d '{"query": "…", "depth": "standard"}'
# stream events (SSE, gapless ids — reconnect with last_event_id):
curl -N "http://localhost:8000/api/research/<run_id>/stream"
curl http://localhost:8000/api/research/<run_id>/report
```

**Presets** (times are observed on a local LM Studio model, one run at a time):

| Preset | Cap (tasks / iterations / sources per task / tool calls) | Observed wall time |
|--------|:---:|:---:|
| QUICK | 3 / 1 / 5 / 20 | ~11 min |
| STANDARD | 6 / 2 / 8 / 60 | ~15–24 min |
| DEEP | 10 / 3 / 12 / 120 | not yet exercised |

**Limits:** runs execute sequentially (`RESEARCH_MAX_CONCURRENT`, default 1). Pages are fetched as plain text — JavaScript-rendered sites may yield little. Default policy is **local-only**; the modal's **SMART MODEL** dropdown can pin the smart role to a specific local or cloud model per run (a manual pick bypasses policy and size caps; `AUTO (POLICY)` keeps the automatic local pick). Runs fail cleanly (`insufficient_sources`, budget exhaustion, toolbox/searxng outages) — the failure is a terminal state with an error event, never a crash. `config_overrides` only ever *lower* the preset caps (e.g. `{"tool_calls": 5, "tasks": 2}`).

Backups (`/api/export`) include `data/research/` automatically — restores bring reports and run history back.

## Cloud providers (OpenRouter etc.)

Beyond local models, you can add an API-key provider and use both from one picker:

1. Open the **Providers** sidebar → `[ + ADD PROVIDER ]` → select **`(•) Cloud API`**.
2. Pick a **preset** (OpenRouter, OpenAI, Groq, Together) — the base URL pre-fills. Override with an `https://` URL if needed.
3. Paste your **API key** (with the `[SHOW]` toggle) → `[ VALIDATE & SAVE ]`.
   - The key is validated against the provider before it is saved (a bad key → 400, nothing stored).
   - It is stored **Fernet-encrypted** in Postgres; only a hint like `sk-or-····8f2a` is ever returned to the UI or logs.
4. The model dropdown splits into `── LOCAL ──` / `── CLOUD ──` groups. Free (`:free`) models get a `free` chip; `★` pins any model into a `── PINNED ──` group shown first. Cloud replies get a ☁ marker and a cost footnote (e.g. `~$0.0004`) when the provider reports token usage.

Privacy: the first time a cloud model is activated, a one-time notice explains that messages leave your device. Cloud providers store keys encrypted with `SECRET_KEY` — keep `.env` backed up, because a different `SECRET_KEY` makes stored cloud keys undecryptable.

## Backup & restore

Everything worth keeping lives in two places: the `pgdata` volume (providers, conversations, memories, vectors) and `./data` (`soul.md`, uploaded documents). The app packages both.

**Export** (Settings section → F11 → `[ EXPORT ]`, or `GET /api/export`):

- Produces `local-ai-hub-export-<ts>.tar.gz` = a pg_dump custom-format `db.dump` (vectors included) + a full copy of `/data` (minus `exports/`). Nothing is stored server-side beyond temp-file cleanup.
- **Include provider keys** toggle (default OFF, safer): with keys ON, the dump contains the Fernet-encrypted `providers` rows; with keys OFF, providers/favorites/app-state are stripped into a secret-free `providers.json` sidecar and re-imported without keys (live keys on the target are preserved).
- **`.env` IS PART OF ANY MIGRATION.** With-keys exports only decrypt on the same `SECRET_KEY`. Restoring on a different `SECRET_KEY` makes provider keys unreadable — carry `.env` (or at least `SECRET_KEY` and `DB_PASSWORD`) alongside the backup. Keys-OFF exports survive a `SECRET_KEY` change but you'll re-enter cloud keys.

**Import** (Settings → `[ PICK FILE ]` → `[ IMPORT ]`, or `POST /api/import?confirm=true`):

- Requires the explicit confirm; archives are validated (tar-slip guard, 200 MB cap, must contain `db.dump`).
- Before touching anything, the current state is snapshotted to `data/exports/pre-import-<ts>.tar.gz` (always with keys) — that file is your rollback.
- During the restore, chat returns `503 restoring backup`; don't write to the app while it runs. Import replaces ALL data (pg_restore `--clean`) and overwrites `/data` files additively.
- The response says `restart_required: true` — restart the stack (`docker compose restart api toolbox`) so DDL re-applies and services re-init.

**Restore on a fresh machine:** clone → `cp .env.example .env` and fill in the **same** `SECRET_KEY` → `docker compose up --build -d` → import the export. Conversations, memories, providers (keys decrypt), and documents all come back.

**Retention & housekeeping** (Settings → HOUSEKEEPING):

- *Archive chats older than N months* (default OFF): `[ ARCHIVE NOW ]` exports matching conversations + messages to `data/exports/archive-<ts>.json` (readable JSON), then deletes them from the database.
- `[ VACUUM NOW ]` runs `VACUUM ANALYZE` on the database.
- Snapshots and archives accumulate in `data/exports/` — prune old ones manually.

## Development (live reload)

```bash
docker compose -f compose.yaml -f compose.dev.yaml up --build -d
```

Python edits trigger uvicorn reload, `server.js` edits restart Node, static files update instantly. Dependency changes (`requirements.txt`, `package.json`) still require `--build`.

## Configuration reference

| Variable | Required | Purpose |
|----------|:---:|---------|
| `DB_PASSWORD` | yes | Postgres password (set in compose's `DATABASE_URL`) |
| `SECRET_KEY` | yes | Fernet key — encrypts cloud API keys at rest |
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
| `/api/conversations/{id}` | PATCH / DELETE | Rename / delete a conversation |
| `/api/conversations/{id}/messages` | GET | Message history for a conversation |
| `/api/memories` | GET | List memory facts (`?q=`, `?category=`) |
| `/api/memories/{id}` | PUT / DELETE | Edit (marks `edited`) / delete a fact |
| `/api/memories/forget_all` | POST | Purge all memory facts |
| `/api/soul` | GET / PUT | Read / write the persona file (mtime tracked) |
| `/api/documents` | POST | Multipart upload — returns `{ id, status: "pending" }` |
| `/api/documents` | GET | List uploaded documents with status + chunk count |
| `/api/documents/{id}` | GET | Document detail + first 3 chunk previews |
| `/api/documents/{id}` | DELETE | Delete file + cascaded chunk vectors |
| `/api/documents/{id}/reingest` | POST | Re-run the ingest pipeline |
| `/api/rag/toggle_auto` | POST | Flip the automatic RAG retrieval flag |
| `/api/export` | GET | Stream `export.tar.gz` (`?include_keys=true` to include encrypted keys) |
| `/api/import` | POST | Restore from an export (`?confirm=true`, multipart `file`) |
| `/api/settings/retention` | GET / PUT | Read / set chat retention in months (null = off) |
| `/api/retention/archive` | POST | Archive + delete conversations older than the retention window |
| `/api/maintenance/vacuum` | POST | `VACUUM ANALYZE` the database |
| `/api/research` | POST | Start a run — `{ "query", "depth"?: "quick"\|"standard"\|"deep", "model_policy"?: "local_only"\|"allow_cloud", "conversation_id"?, "config_overrides"?, "model_override"?: {provider_id, model} }` → 201 `{ run_id, status: "queued" }`. `model_override` pins the smart role to a specific provider model (bypasses policy/caps — a manual pick; 400 on unknown provider id or empty model; omit it to keep the AUTO policy pick) |
| `/api/research` | GET | List runs (`?status=`, `?limit=`) with counts + model |
| `/api/research/{run_id}` | GET | Run detail: plan, tasks, counts, metrics, model, error |
| `/api/research/{run_id}/stream` | GET | SSE event stream (`?last_event_id=`) — status/plan/search/fetch/note/reflect/write/verify/done |
| `/api/research/{run_id}/cancel` | POST | Cancel a running run |
| `/api/research/{run_id}/resume` | POST | Re-queue an `interrupted` or `failed` run |
| `/api/research/{run_id}/sources` | GET | Sources with citation `n`, url, title, fetch status, excerpt |
| `/api/research/{run_id}/report` | GET | The report as markdown |
| `/api/research/{run_id}` | DELETE | Delete a run (terminal states only) |
| `/api/tools` | GET | List MCP tools with health + enabled state |
| `/api/tools/{name}/toggle` | POST | Enable / disable a tool |
| `/api/tools/{name}/test` | POST | Run a tool with `{ "args": {...} }` |

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
    docs/                   # uploaded documents (gitignored)
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

- **Postgres** — queryable data (providers, conversations, messages) lives in the `pgdata` Docker volume. Use the built-in export (above) for backups; raw `docker compose exec db pg_dump -U localai localai` works too.
- **`./data`** — human-editable files (e.g. `soul.md`, the assistant persona). It is a bind mount, so it's a plain folder on your host; gitignored content is never committed. `data/research/` holds generated research reports; `data/exports/` holds import snapshots and retention archives.
- All services run `restart: unless-stopped`; prod compose has no source bind mounts and no `--reload`/`--watch` (use `compose.dev.yaml` for development).

## Security & privacy

- Published ports bind to `127.0.0.1` only — no authentication, single-user localhost app.
- `.env` holds secrets and is gitignored — never commit or share it.
- API keys are encrypted at rest (Phase 2) and never returned to the frontend or logged.
- Cloud provider base URLs must be HTTPS.

## Debugging the MCP toolbox

The `toolbox` service (FastMCP, streamable HTTP) is internal-only — nothing is published in production. To inspect its tools directly, use the [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector):

**Dev mode** (`compose.dev.yaml` publishes `127.0.0.1:9000`):

```bash
npx @modelcontextprotocol/inspector
# in the UI: Transport type = Streamable HTTP, URL = http://localhost:9000/mcp
```

**Production mode** — run the inspector in a throwaway container on the app network:

```bash
docker run -it --rm --network local-ai-hub_appnet -p 127.0.0.1:6274:6274 \
  node:20-alpine npx -y @modelcontextprotocol/inspector
# in the UI: Transport type = Streamable HTTP, URL = http://toolbox:9000/mcp
# if the browser can't connect, add:  -e HOST=0.0.0.0
```

From the inspector you can list tools (expect `web.search`, `web.fetch`, `arxiv.search`, `rag.search`, `rag.ingest`, `util.datetime`), view their input schemas, and call them with custom args — useful for isolating whether a problem is in the tool, the backend client, or the LLM.

### Toolbox tools

| Tool | Purpose | Notes |
|------|---------|-------|
| `web.search` | Search the web via the self-hosted SearXNG instance. | Always returns live results. |
| `web.fetch` | Fetch a URL, extract main readable text, and cache it in Postgres (`web_cache`). | `extract: auto` uses trafilatura; `extract: raw` uses the legacy HTML strip. Cached entries expire after `cache_ttl_hours` (default 24). |
| `arxiv.search` | Search arXiv preprints (no API key). | Defaults to **disabled** in the Tools sidebar; toggle it on before use. |
| `rag.search` / `rag.ingest` | Search or add notes to the uploaded-document vector store. | Requires host Ollama with `nomic-embed-text`. |
| `util.datetime` | Current date/time. | Call first when reasoning about "latest/today/recent". |

Quick toolbox troubleshooting without the inspector:

```bash
curl localhost:8000/api/tools                                   # live health + enabled state per tool
curl -X POST localhost:8000/api/tools/web.search/test \
  -H 'content-type: application/json' -d '{"args":{"query":"ollama"}}'
```

| Symptom | Likely cause |
|---------|--------------|
| Tools show `"health":"degraded"` | Toolbox container down/restarting — the backend reconnects automatically; check `docker compose logs toolbox`. |
| Tool list empty | Backend never connected at startup; check `MCP_TOOLBOX_URL` and `docker compose logs api`. |
| `web.search` returns nothing / errors | SearXNG JSON format disabled — `searxng/settings.yml` must contain `formats: [html, json]`; also check `SEARXNG_SECRET` is set and not the literal `${SEARXNG_SECRET}`. |
| `web.fetch` hangs | Per-tool 30s cap should fire; if it doesn't, `docker compose restart toolbox`. |

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Provider shows `down` / `provider unreachable` | Ollama/LM Studio must bind to `0.0.0.0` (see Host networking) and be running. |
| `no_model` on chat | No model activated yet — activate one in the Providers section first. |
| `provider_down` on chat | The activated provider is not responding; check it after re-detect (F5). |
| First reply is very slow | Normal — model is loading into memory. Keep it activated to avoid repeat warmups. |
| Port already in use | Stop whatever occupies `3000`/`8000`, or change the mapping in `compose.yaml`. |
| `docker compose config` fails | `docker compose v2` is required; `.env` must exist with all `${VAR}` references. |
| Uploaded document stays `failed` | Ollama must be running and `nomic-embed-text` pulled; check `docker compose logs api`. The original file is kept on disk so you can reingest after fixing Ollama. |
| Citation marker opens no popover | Citations are model-dependent; if the model did not emit `[n]`/`【n】`, the frontend has nothing to link. Try a model that follows instructions well (e.g., `gpt-4o-mini`). |
