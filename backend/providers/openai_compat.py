import time

import httpx

from .base import PING_TIMEOUT, ModelInfo, ProviderBase, ProviderStatus, fmt_err


class OpenAICompatProvider(ProviderBase):
    async def _list(self, timeout: float) -> list[ModelInfo]:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{self.base_url}/v1/models")
            response.raise_for_status()
        return [ModelInfo(id=m["id"]) for m in response.json().get("data", [])]

    async def ping(self) -> ProviderStatus:
        start = time.monotonic()
        try:
            models = await self._list(PING_TIMEOUT)
            latency = int((time.monotonic() - start) * 1000)
            return ProviderStatus(
                state="up" if models else "up_empty",
                latency_ms=latency,
                models=models,
            )
        except Exception as exc:
            return ProviderStatus(state="down", error=fmt_err(exc))

    async def models(self) -> list[ModelInfo]:
        return await self._list(5.0)
