import logging
from typing import Any

import db
from mcp_client.manager import MCPManager

logger = logging.getLogger(__name__)


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
