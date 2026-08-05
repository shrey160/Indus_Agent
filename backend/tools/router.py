import logging
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db
from mcp_client import manager
from tools_registry import call_tool, get_tools, is_enabled, toggle_tool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["tools"])


class ToolTestRequest(BaseModel):
    args: dict


@router.get("/tools")
async def list_tools() -> list[dict]:
    return await get_tools(manager)


@router.post("/tools/{name}/toggle")
async def toggle(name: str) -> dict:
    new_state = await toggle_tool(name)
    return {"name": name, "enabled": new_state}


@router.post("/tools/{name}/test")
async def test_tool(name: str, body: ToolTestRequest) -> dict:
    if not await is_enabled(name):
        raise HTTPException(status_code=400, detail="tool is disabled")
    start = time.monotonic()
    try:
        result = await call_tool(manager, name, body.args)
        latency_ms = int((time.monotonic() - start) * 1000)
        return {"ok": True, "result": result, "latency_ms": latency_ms, "error": None}
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        logger.warning("tool test failed: %s", exc)
        return {
            "ok": False,
            "result": None,
            "latency_ms": latency_ms,
            "error": str(exc) or type(exc).__name__,
        }
