# Local AI Hub

Self-hosted, local-first AI chat hub. Phase 1: core chat with local providers (Ollama / LM Studio), streaming SSE, Postgres history.

## Environment

Copy `.env.example` to `.env` and fill in real values (never commit `.env`):

| Variable | Required | Purpose |
|----------|----------|---------|
| `DB_PASSWORD` | yes | Postgres password (generated at setup) |
| `SECRET_KEY` | yes | Fernet key — encrypts cloud API keys at rest (Phase 2) |
| `TEST_OPENROUTER_KEY` | optional | OpenRouter key used for Phase 2 acceptance testing |
| `OPENROUTER_ENDPOINT` | optional | Override OpenRouter base URL for testing |
| `ALLOWED_MODEL_OPENROUTER` | optional | Restrict OpenRouter models during testing |

The `TEST_/OPENROUTER_` variables are not read by the app yet — they arrive with Phase 2.

## Quickstart

Production:

```bash
docker compose up --build -d
```

Dev (live reload — Python edits trigger uvicorn reload, `server.js` edits restart Node, static files update instantly):

```bash
docker compose -f compose.yaml -f compose.dev.yaml up --build -d
```

Dependency changes (`requirements.txt`, `package.json`) still require `--build`.

Verify:

```bash
curl localhost:8000/health
curl localhost:8000/api/info
curl localhost:3000/
```

Stop: `docker compose down` (keeps the `pgdata` volume). Never run `down -v` unless you intend to wipe the database.

## Host networking (Ollama/LM Studio)

Containers reach LLM servers running on the host via `host.docker.internal` (compose maps it automatically). Both Ollama and LM Studio bind to `127.0.0.1` by default, which containers **cannot** reach — you must bind them to `0.0.0.0`:

- **Windows/macOS (Docker Desktop):** `host.docker.internal` works out of the box.
  - Ollama: set the system environment variable `OLLAMA_HOST=0.0.0.0`, then restart Ollama.
  - LM Studio: Server tab → enable "Serve on local network".
- **Linux:** compose already adds `host-gateway`.
  - Ollama: `systemctl edit ollama` → add `[Service]` with `Environment="OLLAMA_HOST=0.0.0.0"` → `systemctl restart ollama`.

> **Warning:** binding `0.0.0.0` exposes the LLM server to your LAN. Restrict with a firewall if unwanted.
