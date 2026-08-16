# Indus Agent — SearXNG config

Self-hosted metasearch backend used by the `web.search` MCP tool. Runs **internal-only**
(`searxng:8080`, never published outside Docker). Settings live in `settings.yml`; the
`SEARXNG_SECRET` env var (from `.env`) is mapped by compose — **do not** put custom YAML
tags like `!ENV` in the file (see HP-011).

## Requirements
- `settings.yml` MUST keep `formats: [html, json]` or `web.search` silently returns nothing.
- After any edit: `docker compose up -d searxng` to prove it still boots.