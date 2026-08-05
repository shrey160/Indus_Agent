import time

import httpx

from .base import (
    ModelInfo,
    ProviderBase,
    ProviderHTTPError,
    ProviderStatus,
    fmt_err,
)

CLOUD_PING_TIMEOUT = 8.0
MODELS_TIMEOUT = 15.0


class CloudProvider(ProviderBase):
    """OpenAI-compatible cloud provider with a Bearer API key."""

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        preset: str | None = None,
    ):
        super().__init__(name, base_url)
        self.api_key = api_key
        self.preset = preset

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _models_path(self) -> str:
        return f"{self.base_url}/models"

    def _chat_path(self) -> str:
        return f"{self.base_url}/chat/completions"

    @staticmethod
    def _is_free(mid: str, pricing) -> bool:
        if mid.endswith(":free"):
            return True
        if isinstance(pricing, dict):
            values = [v for v in pricing.values() if v is not None]
            return bool(values) and all(v in (0, "0", "0.0") for v in values)
        return False

    async def ping(self) -> ProviderStatus:
        start = time.monotonic()
        headers = self._auth_headers()
        try:
            async with httpx.AsyncClient(timeout=CLOUD_PING_TIMEOUT) as client:
                if self.preset == "openrouter":
                    # OpenRouter /models is public (200 even with a bad key);
                    # /credits is the authenticated gate used for key validation.
                    cred = await client.get(f"{self.base_url}/credits", headers=headers)
                    if cred.status_code == 401:
                        return ProviderStatus(
                            state="bad_key",
                            latency_ms=int((time.monotonic() - start) * 1000),
                            error="invalid API key",
                        )
                    if cred.status_code == 402:
                        return ProviderStatus(
                            state="no_credits",
                            latency_ms=int((time.monotonic() - start) * 1000),
                            error="no credits — payment required",
                        )
                    if cred.status_code == 429:
                        return ProviderStatus(
                            state="down",
                            latency_ms=int((time.monotonic() - start) * 1000),
                            error="rate limited",
                        )
                    if cred.status_code >= 400:
                        return ProviderStatus(
                            state="down",
                            latency_ms=int((time.monotonic() - start) * 1000),
                            error=cred.text.strip()[:200] or f"HTTP {cred.status_code}",
                        )
                    balance = None
                    try:
                        total = ((cred.json().get("data") or {}).get("total_credits"))
                        if total is not None:
                            balance = f"${float(total):.2f}"
                    except Exception:
                        balance = None
                    response = await client.get(
                        self._models_path(), headers=headers
                    )
                else:
                    balance = None
                    response = await client.get(
                        self._models_path(), headers=headers
                    )
                    if response.status_code == 401:
                        return ProviderStatus(
                            state="bad_key",
                            latency_ms=int((time.monotonic() - start) * 1000),
                            error="invalid API key",
                        )
                    if response.status_code == 402:
                        return ProviderStatus(
                            state="no_credits",
                            latency_ms=int((time.monotonic() - start) * 1000),
                            error="no credits — payment required",
                        )
                    if response.status_code == 429:
                        return ProviderStatus(
                            state="down",
                            latency_ms=int((time.monotonic() - start) * 1000),
                            error="rate limited",
                        )
                    if response.status_code >= 400:
                        return ProviderStatus(
                            state="down",
                            latency_ms=int((time.monotonic() - start) * 1000),
                            error=response.text.strip()[:200] or f"HTTP {response.status_code}",
                        )
            latency = int((time.monotonic() - start) * 1000)
            items = response.json().get("data", [])
            models = [
                ModelInfo(
                    id=m.get("id", ""),
                    pricing=m.get("pricing"),
                    context_length=m.get("context_length"),
                    is_free=self._is_free(m.get("id", ""), m.get("pricing")),
                )
                for m in items
            ]
            return ProviderStatus(
                state="up" if models else "up_empty",
                latency_ms=latency,
                models=models,
                balance=balance,
            )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            return ProviderStatus(state="unreachable", error=fmt_err(exc))
        except Exception as exc:
            return ProviderStatus(state="unreachable", error=fmt_err(exc))

    async def models(self) -> list[ModelInfo]:
        headers = self._auth_headers()
        async with httpx.AsyncClient(timeout=MODELS_TIMEOUT) as client:
            response = await client.get(self._models_path(), headers=headers)
        if response.status_code == 401:
            raise ProviderHTTPError(401, "invalid API key")
        if response.status_code == 402:
            raise ProviderHTTPError(402, "no credits — payment required")
        if response.status_code == 429:
            raise ProviderHTTPError(429, "rate limited")
        if response.status_code >= 400:
            raise ProviderHTTPError(
                response.status_code, response.text.strip()[:200] or "request failed"
            )
        return [
            ModelInfo(
                id=m.get("id", ""),
                pricing=m.get("pricing"),
                context_length=m.get("context_length"),
                is_free=self._is_free(m.get("id", ""), m.get("pricing")),
            )
            for m in response.json().get("data", [])
        ]
