import time

import httpx

from .base import PING_TIMEOUT, ModelInfo, ProviderBase, ProviderStatus, fmt_err


class _AuthError(Exception):
    """Raised when the local OpenAI-compatible endpoint rejects the API key."""


class OpenAICompatProvider(ProviderBase):
    def __init__(self, name: str, base_url: str, api_key: str = ""):
        super().__init__(name, base_url)
        self.api_key = api_key

    def _auth_headers(self) -> dict[str, str]:
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    async def _list(self, timeout: float) -> list[ModelInfo]:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(self._models_path(), headers=self._auth_headers())
            if response.status_code == 401:
                raise _AuthError("invalid API key")
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
        except _AuthError as exc:
            return ProviderStatus(
                state="bad_key",
                latency_ms=int((time.monotonic() - start) * 1000),
                error=str(exc),
            )
        except Exception as exc:
            return ProviderStatus(state="down", error=fmt_err(exc))

    async def models(self) -> list[ModelInfo]:
        return await self._list(5.0)
