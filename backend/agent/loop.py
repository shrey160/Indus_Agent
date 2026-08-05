import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator

from mcp_client.manager import MCPManager
from providers.base import fmt_err
from tools_registry import call_tool, get_tools

from .fallback_parser import parse_tool_calls

logger = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 4
TOOL_TIMEOUT_S = 30.0

USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens")


def _merge_usage(total: dict | None, delta: dict | None) -> dict | None:
    if not delta:
        return total
    merged = dict(total or {})
    for key in USAGE_KEYS:
        value = delta.get(key)
        if value is not None:
            merged[key] = (merged.get(key) or 0) + value
    return merged


def _sanitized(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)


def _tool_schemas(tools: list[dict]) -> tuple[list[dict], dict[str, str]]:
    schemas = []
    name_map = {}
    for t in tools:
        safe = _sanitized(t["name"])
        name_map[safe] = t["name"]
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": safe,
                    "description": t.get("description") or "",
                    "parameters": t.get("params_schema")
                    or {"type": "object", "properties": {}},
                },
            }
        )
    return schemas, name_map


def _fallback_instructions(schemas: list[dict]) -> str:
    lines = [
        f'- {s["function"]["name"]}: {s["function"]["description"]} '
        f'parameters={json.dumps(s["function"]["parameters"])}'
        for s in schemas
    ]
    return (
        "<tools>\n"
        "You can call tools by replying with ONLY a JSON object in exactly this format: "
        '{"name": "<tool name>", "arguments": {<arguments>}}.\n'
        "Available tools:\n" + "\n".join(lines) + "\n"
        "Emit the JSON object alone (no markdown fences, no extra text) when you need a tool. "
        "After you receive the tool result, answer the user normally.\n"
        "</tools>"
    )


def _preview(payload: dict) -> dict:
    preview = {}
    for key, value in payload.items():
        if isinstance(value, list):
            preview[key] = value[:3]
            preview[f"{key}_count"] = len(value)
        elif isinstance(value, str) and len(value) > 200:
            preview[key] = value[:200] + "…"
        else:
            preview[key] = value
    return preview


async def _enabled_healthy_tools(manager: MCPManager) -> list[dict]:
    try:
        tools = await get_tools(manager)
    except Exception:
        logger.warning("tool registry unavailable; chatting without tools", exc_info=True)
        return []
    return [t for t in tools if t.get("enabled") and t.get("health") == "ok"]


async def run(
    provider,
    model: str,
    messages: list[dict],
    manager: MCPManager,
    *,
    native_tools: bool,
) -> AsyncIterator[dict]:
    tools = await _enabled_healthy_tools(manager)
    schemas, name_map = _tool_schemas(tools)

    convo = list(messages)
    if schemas and not native_tools:
        instructions = _fallback_instructions(schemas)
        if convo and convo[0].get("role") == "system":
            convo[0] = {**convo[0], "content": convo[0]["content"] + "\n\n" + instructions}
        else:
            convo.insert(0, {"role": "system", "content": instructions})

    total_usage: dict | None = None
    sources: list[dict] = []
    seen_urls: set[str] = set()
    iterations = 0

    while True:
        text = ""
        calls: list[dict] = []
        async for event in provider.stream_chat(
            model,
            convo,
            tools=schemas if native_tools and schemas else None,
            tool_choice="auto" if native_tools and schemas else None,
        ):
            etype = event["type"]
            if etype == "content":
                text += event["text"]
                yield event
            elif etype == "reasoning":
                yield event
            elif etype == "usage":
                total_usage = _merge_usage(total_usage, event["usage"])
                yield {"type": "usage", "usage": total_usage}
            elif etype == "tool_calls":
                calls.extend(event["calls"])

        if not calls and schemas and not native_tools:
            cleaned, parsed = parse_tool_calls(text)
            if parsed:
                calls = parsed
                text = cleaned

        if not calls or not schemas:
            yield {"type": "final", "text": text, "sources": sources}
            return

        iterations += 1
        if iterations > MAX_TOOL_ITERATIONS:
            logger.warning("tool loop limit reached (%d)", MAX_TOOL_ITERATIONS)
            yield {"type": "tool_limit", "detail": "tool loop limit reached"}
            final_text = ""
            async for event in provider.stream_chat(model, convo):
                etype = event["type"]
                if etype == "content":
                    final_text += event["text"]
                    yield event
                elif etype == "reasoning":
                    yield event
                elif etype == "usage":
                    total_usage = _merge_usage(total_usage, event["usage"])
                    yield {"type": "usage", "usage": total_usage}
            yield {"type": "final", "text": final_text, "sources": sources}
            return

        if native_tools:
            convo.append(
                {
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": [
                        {
                            "id": c.get("id") or f"call_{i}",
                            "type": "function",
                            "function": {
                                "name": c["name"],
                                "arguments": json.dumps(c.get("arguments") or {}),
                            },
                        }
                        for i, c in enumerate(calls)
                    ],
                }
            )
        else:
            convo.append({"role": "assistant", "content": text})

        fallback_results: list[str] = []
        for i, call in enumerate(calls):
            name = name_map.get(call.get("name") or "", call.get("name") or "")
            args = call.get("arguments") or {}
            yield {"type": "tool", "tool": {"name": name, "args": args, "status": "running"}}
            start = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    call_tool(manager, name, args), timeout=TOOL_TIMEOUT_S
                )
                error = None
            except asyncio.TimeoutError:
                result, error = None, f"tool timed out after {int(TOOL_TIMEOUT_S)}s"
            except Exception as exc:
                logger.warning("tool %s failed: %s", name, fmt_err(exc))
                result, error = None, fmt_err(exc)
            latency_ms = int((time.monotonic() - start) * 1000)
            payload = result if isinstance(result, dict) else {"error": error}
            if error and isinstance(result, dict):
                payload = {**result, "error": error}
            if name == "web.search" and isinstance(result, dict):
                for r in result.get("results") or []:
                    url = r.get("url")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        sources.append({"title": r.get("title"), "url": url})
            yield {
                "type": "tool",
                "tool": {
                    "name": name,
                    "status": "done",
                    "latency_ms": latency_ms,
                    "result_preview": _preview(payload),
                    "error": error,
                },
            }
            if native_tools:
                convo.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or f"call_{i}",
                        "content": json.dumps(payload),
                    }
                )
            else:
                fallback_results.append(f"Tool result for {name}:\n{json.dumps(payload)}")
        if not native_tools:
            convo.append({"role": "user", "content": "\n\n".join(fallback_results)})
