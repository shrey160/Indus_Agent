# MCP client manager for the toolbox server.
import asyncio
import logging
import os
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger(__name__)

MCP_TOOLBOX_URL = os.environ.get("MCP_TOOLBOX_URL", "http://toolbox:9000/mcp")


class MCPManager:
    def __init__(self, url: str | None = None):
        self.url = url or MCP_TOOLBOX_URL
        self._session: ClientSession | None = None
        self._tools: list[Any] = []
        self._health: str = "degraded"
        self._session_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._backoff = 1.0

    async def start(self) -> None:
        if self._session_task is not None and not self._session_task.done():
            return
        self._session_task = asyncio.create_task(self._session_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        await self._cancel_tasks()

    async def _cancel_tasks(self) -> None:
        for t in (self._session_task, self._reconnect_task):
            if t is not None and not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
        self._session_task = None
        self._reconnect_task = None

    async def _session_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                logger.info("connecting to MCP toolbox at %s", self.url)
                async with streamablehttp_client(self.url) as (
                    read_stream,
                    write_stream,
                    _get_session_id,
                ):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        self._session = session
                        tools_result = await session.list_tools()
                        self._tools = list(getattr(tools_result, "tools", []) or [])
                        self._health = "ok"
                        self._backoff = 1.0
                        logger.info(
                            "MCP toolbox connected; tools=%s",
                            [t.name for t in self._tools],
                        )
                        # Hold the session open until shutdown or disconnect.
                        await self._stop_event.wait()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("MCP toolbox session lost: %s", exc)
            finally:
                self._session = None
                self._health = "degraded"

            if self._stop_event.is_set():
                break

            logger.info("MCP toolbox reconnecting in %.1fs", self._backoff)
            await asyncio.sleep(self._backoff)
            self._backoff = min(self._backoff * 2, 30.0)

    async def refresh(self) -> None:
        """Probe the live session and update cached tools. On failure, force reconnect."""
        if self._session is None:
            self._health = "degraded"
            self._schedule_reconnect()
            return
        try:
            tools_result = await asyncio.wait_for(
                self._session.list_tools(), timeout=5.0
            )
            self._tools = list(getattr(tools_result, "tools", []) or [])
            self._health = "ok"
            self._backoff = 1.0
        except Exception as exc:
            logger.warning("MCP toolbox refresh failed: %s", exc)
            self._health = "degraded"
            await self._cancel_tasks()
            self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        while self._health != "ok" and not self._stop_event.is_set():
            await asyncio.sleep(self._backoff)
            self._backoff = min(self._backoff * 2, 30.0)
            try:
                await self.start()
                # Wait briefly for the new session loop to connect.
                for _ in range(25):
                    if self._health == "ok":
                        break
                    await asyncio.sleep(0.2)
                if self._health == "ok":
                    break
            except Exception:
                pass

    async def ready(self, timeout: float | None = None) -> bool:
        if self._health == "ok" and self._session is not None:
            return True
        if timeout:
            for _ in range(int(timeout / 0.2)):
                if self._health == "ok" and self._session is not None:
                    return True
                await asyncio.sleep(0.2)
        return self._health == "ok" and self._session is not None

    def health(self) -> str:
        return self._health

    async def list_tools(self) -> list[Any]:
        return self._tools

    async def call_tool(self, name: str, arguments: dict) -> Any:
        if self._session is None:
            raise RuntimeError("MCP toolbox not connected")
        try:
            return await self._session.call_tool(name, arguments=arguments)
        except Exception:
            self._health = "degraded"
            self._schedule_reconnect()
            raise


# Module-level singleton.  lifespan in main.py calls start()/stop().
manager = MCPManager()
