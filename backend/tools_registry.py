import logging
from typing import Any

import db
from mcp_client.manager import MCPManager

logger = logging.getLogger(__name__)


KNOWN_TOOLS: dict[str, str] = {
    "web.search": "Search the web via local SearXNG. Returns {query, results: [{title, url, snippet}], source}.",
    "web.fetch": "Fetch a URL and return cleaned readable text. Returns {url, title, text, truncated, source}.",
    "util.datetime": "Current date/time. Always call before reasoning about 'latest/today/recent'.",
    "rag.search": "Semantic search over the user's uploaded documents. Returns {query, results: [{doc, chunk_id, snippet, score}], source: 'rag'}.",
    "rag.ingest": "Index an ad-hoc text note into the document store for later rag.search.",
}


async def ensure_tool_rows(tool_names: list[str]) -> dict[str, bool]:
    rows = await db.fetch("SELECT tool_name, enabled FROM tool_settings")
    enabled = {r["tool_name"]: r["enabled"] for r in rows}
    for name in tool_names:
        if name not in enabled:
            await db.execute(
                "INSERT INTO tool_settings (tool_name, enabled) VALUES ($1, TRUE)",
                name,
            )
            enabled[name] = True
    return enabled


async def get_tools(manager: MCPManager) -> list[dict]:
    await manager.refresh()
    raw_tools = await manager.list_tools()
    names = [t.name for t in raw_tools]
    enabled_map = await ensure_tool_rows(names)
    # If the toolbox is unreachable, make sure we still have rows for known tools
    # so the endpoint can report degraded instead of an empty list.
    if not raw_tools or manager.health() != "ok":
        enabled_map.update(await ensure_tool_rows(list(KNOWN_TOOLS.keys())))

    tools = []
    for tool in raw_tools:
        enabled = enabled_map.get(tool.name, True)
        health = manager.health()
        if not enabled:
            health = "disabled"
        elif health != "ok":
            health = "degraded"
        tools.append(
            {
                "name": tool.name,
                "description": tool.description,
                "params_schema": tool.inputSchema,
                "enabled": enabled,
                "health": health,
                "server": "toolbox",
            }
        )

    # If the manager could not refresh the live list, fall back to known tool rows
    # stored in the database so the registry never silently returns [].
    known_names = set(KNOWN_TOOLS.keys()) - {t["name"] for t in tools}
    if known_names and manager.health() != "ok":
        rows = await db.fetch(
            "SELECT tool_name, enabled FROM tool_settings WHERE tool_name = ANY($1)",
            list(known_names),
        )
        db_enabled = {r["tool_name"]: r["enabled"] for r in rows}
        for name in known_names:
            enabled = db_enabled.get(name, True)
            health = "disabled" if not enabled else "degraded"
            tools.append(
                {
                    "name": name,
                    "description": KNOWN_TOOLS[name],
                    "params_schema": {},
                    "enabled": enabled,
                    "health": health,
                    "server": "toolbox",
                }
            )
    return tools


async def toggle_tool(name: str) -> bool:
    row = await db.fetchrow(
        "SELECT enabled FROM tool_settings WHERE tool_name = $1", name
    )
    if row is None:
        # Unknown tool; still create row defaulting to True then toggle to False.
        await db.execute(
            "INSERT INTO tool_settings (tool_name, enabled) VALUES ($1, TRUE)", name
        )
        new_state = False
    else:
        new_state = not row["enabled"]
    await db.execute(
        "UPDATE tool_settings SET enabled = $1 WHERE tool_name = $2",
        new_state,
        name,
    )
    return new_state


async def is_enabled(name: str) -> bool:
    row = await db.fetchval(
        "SELECT enabled FROM tool_settings WHERE tool_name = $1", name
    )
    return row if row is not None else True


def _extract_text(result: Any) -> dict:
    text_parts = []
    for item in getattr(result, "content", []) or []:
        if getattr(item, "type", None) == "text":
            text_parts.append(getattr(item, "text", ""))
    joined = "\n".join(text_parts)
    try:
        import json

        parsed = json.loads(joined)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {"text": joined}


async def call_tool(manager: MCPManager, name: str, arguments: dict) -> dict:
    result = await manager.call_tool(name, arguments)
    return _extract_text(result)
