# Indus Agent — Frontend (zero-dependency Node + vanilla JS)

## Overview
Static-file server + API proxy for the Indus Agent UI. `node:20-alpine` image, **zero npm
dependencies** (no express, no build step, no framework). Serves `public/` and proxies
`/api/*` → `http://api:8000/*` with unbuffered SSE passthrough (token-by-token streaming).

## Layout
```
frontend/
  Dockerfile          — node:20-alpine, npm ci (package-lock.json required)
  package.json        — zero runtime deps
  server.js           — static server + /api proxy (per-request AbortController)
  public/
    index.html        — entry page
    css/              — token-driven CSS (Bloomberg-terminal theme)
      tokens.css      — design tokens (--bg, --accent, spacing, radii)
      base.css        — reset + base styles
      layout.css      — app shell: top bar, sidebar, chat area
      components.css  — cards, buttons, modals, tool chips, tables, research view
    js/
      app.js          — boot, top bar, F-key shortcuts, global hooks
      sidebar.js      — collapsible sidebar sections + delegation
      chat.js         — SSE chat client, tool chips, citations, markdown render
      providers.js    — local/cloud provider management
      tools.js        — MCP tools panel (health dots, test console)
      conversations.js— conversation list + titles
      documents.js    — upload/ingest/delete documents (RAG)
      memory.js       — Soul viewer/editor + facts
      research.js     — deep-research runs (F12) + rendered markdown reports
      settings.js     — backup/settings panel (F11)
      markdown.js     — allowlist markdown renderer (shared, XSS-safe)
      ui.js           — el/toast/modal primitives
```

## Key behaviors
- `server.js` guards path traversal (`path.normalize` + `startsWith`), forwards
  `content-disposition`, and aborts the upstream fetch when the browser disconnects —
  never pipe SSE without a close handler (HP-004).
- XSS rule: user/LLM content is rendered via `textContent` / `window.MD.render` —
  never raw `innerHTML`.
- All API calls go through the relative `/api/...` proxy (no CORS needed).

## Run
Normally launched via compose (root `README.md`). For standalone dev:
```bash
node server.js            # serves on :3000, proxies /api → api:8000
```

## F-keys
`F1` help · `F2` sidebar · `F4` new chat · `F5` re-detect providers ·
`F9` upload documents · `F10` log filter · `F11` export/settings · `F12` research ·
`Ctrl/Cmd+K` model picker · `Esc` dismiss.