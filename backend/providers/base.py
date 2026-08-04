import asyncio
import json
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx

PING_TIMEOUT = 1.5
TEST_TIMEOUT = 20.0
CHAT_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=10.0)
WARMUP_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=10.0)


def fmt_err(exc: Exception) -> str:
    msg = str(exc)
    return msg if msg else type(exc).__name__


@dataclass
class ModelInfo:
    id: str
    size_bytes: int | None = None


@dataclass
class ProviderStatus:
    state: str  # 'up' | 'up_empty' | 'down' | 'checking'
    latency_ms: int | None = None
    error: str | None = None
    models: list[ModelInfo] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "models": [{"id": m.id, "size_bytes": m.size_bytes} for m in self.models],
        }


class ProviderBase(ABC):
    def __init__(self, name: str, base_url: str):
        self.name = name
        self.base_url = base_url.rstrip("/")

    @abstractmethod
    async def ping(self) -> ProviderStatus: ...

    @abstractmethod
    async def models(self) -> list[ModelInfo]: ...

    async def stream_chat(
        self, model: str, messages: list[dict]
    ) -> AsyncIterator[dict]:
        async with httpx.AsyncClient(timeout=CHAT_TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                json={"model": model, "messages": messages, "stream": True},
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        return
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    reasoning = delta.get("reasoning") or delta.get("reasoning_content")
                    if reasoning:
                        yield {"type": "reasoning", "text": reasoning}
                    content = delta.get("content")
                    if content:
                        yield {"type": "content", "text": content}

    async def is_loaded(self, model: str) -> bool:
        return False

    async def warmup(self, model: str) -> int:
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=WARMUP_TIMEOUT) as client:
            response = await client.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 1,
                    "stream": False,
                },
            )
            response.raise_for_status()
        return int((time.monotonic() - start) * 1000)

    async def test(self, model: str, prompt: str) -> dict:
        start = time.monotonic()
        try:
            async with asyncio.timeout(TEST_TIMEOUT):
                reply = ""
                async for event in self.stream_chat(
                    model, [{"role": "user", "content": prompt}]
                ):
                    if event["type"] == "content":
                        reply += event["text"]
            return {
                "ok": True,
                "reply": reply,
                "latency_ms": int((time.monotonic() - start) * 1000),
                "error": None,
            }
        except Exception as exc:
            return {
                "ok": False,
                "reply": None,
                "latency_ms": int((time.monotonic() - start) * 1000),
                "error": fmt_err(exc),
            }
