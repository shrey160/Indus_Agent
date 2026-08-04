import time

import httpx

from .base import PING_TIMEOUT, ModelInfo, ProviderBase, ProviderStatus, fmt_err


class OllamaProvider(ProviderBase):
    async def _tags(self, timeout: float) -> list[ModelInfo]:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
        return [
            ModelInfo(id=m["name"], size_bytes=m.get("size"))
            for m in response.json().get("models", [])
        ]

    async def ping(self) -> ProviderStatus:
        start = time.monotonic()
        try:
            models = await self._tags(PING_TIMEOUT)
            latency = int((time.monotonic() - start) * 1000)
            return ProviderStatus(
                state="up" if models else "up_empty",
                latency_ms=latency,
                models=models,
            )
        except Exception as exc:
            return ProviderStatus(state="down", error=fmt_err(exc))

    async def models(self) -> list[ModelInfo]:
        return await self._tags(5.0)

    async def is_loaded(self, model: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=PING_TIMEOUT) as client:
                response = await client.get(f"{self.base_url}/api/ps")
                response.raise_for_status()
            loaded = {m.get("name") or m.get("model") for m in response.json().get("models", [])}
            return model in loaded
        except Exception:
            return False
