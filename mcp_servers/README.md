# Indus Agent — MCP Tool Servers

This directory holds MCP (Model Context Protocol) servers that provide tools to the
agent loop in `backend/`.

- `toolbox/` — the single FastMCP tool server (streamable HTTP) exposing web search,
  page fetching, arXiv search, utilities and RAG tools. See `toolbox/README.md`.