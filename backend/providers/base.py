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


def compute_cost(pricing, usage) -> float | None:
    if not pricing or not usage:
        return None
    try:
        pin = float(pricing.get("prompt", 0))
        pout = float(pricing.get("completion", 0))
    except (TypeError, ValueError):
        return None
    pt = usage.get("prompt_tokens") or 0
    ct = usage.get("completion_tokens") or 0
    if pt == 0 and ct == 0:
        return None
    return round(pt * pin + ct * pout, 6)


class ProviderHTTPError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def _body_peek(response: httpx.Response, limit: int = 300) -> str:
    try:
        chunks = [
            part.decode(errors="replace")
            async for part in response.aiter_bytes()
            if part
        ]
        body = "".join(chunks)
    except Exception:
        body = ""
    body = body.strip()
    if len(body) > limit:
        body = body[:limit] + "…"
    return body or type(response).__name__


@dataclass
class ModelInfo:
    id: str
    size_bytes: int | None = None
    pricing: dict | None = None
    context_length: int | None = None
    is_free: bool | None = None


@dataclass
class ProviderStatus:
    state: str  # 'up' | 'up_empty' | 'down' | 'checking' | 'bad_key' | 'no_credits' | 'unreachable'
    latency_ms: int | None = None
    error: str | None = None
    models: list[ModelInfo] = field(default_factory=list)
    balance: str | None = None

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "models": [
                {
                    "id": m.id,
                    "size_bytes": m.size_bytes,
                    "pricing": m.pricing,
                    "context_length": m.context_length,
                    "is_free": m.is_free,
                }
                for m in self.models
            ],
            "balance": self.balance,
        }


class ProviderBase(ABC):
    def __init__(self, name: str, base_url: str):
        self.name = name
        self.base_url = base_url.rstrip("/")

    def _auth_headers(self) -> dict[str, str]:
        return {}

    def _models_path(self) -> str:
        return f"{self.base_url}/v1/models"

    def _chat_path(self) -> str:
        return f"{self.base_url}/v1/chat/completions"

    @abstractmethod
    async def ping(self) -> ProviderStatus: ...

    @abstractmethod
    async def models(self) -> list[ModelInfo]: ...

    async def stream_chat(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> AsyncIterator[dict]:
        body: dict = {"model": model, "messages": messages, "stream": True}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice or "auto"
        async with httpx.AsyncClient(timeout=CHAT_TIMEOUT) as client:
            async with client.stream(
                "POST",
                self._chat_path(),
                headers=self._auth_headers(),
                json=body,
            ) as response:
                if response.status_code >= 400:
                    raise ProviderHTTPError(
                        response.status_code, fmt_err(await _body_peek(response))
                    )
                pending_tool_calls: dict[int, dict] = {}
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    usage = chunk.get("usage")
                    if usage and (
                        usage.get("prompt_tokens") is not None
                        or usage.get("completion_tokens") is not None
                    ):
                        yield {"type": "usage", "usage": usage}
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    reasoning = delta.get("reasoning") or delta.get("reasoning_content")
                    if reasoning:
                        yield {"type": "reasoning", "text": reasoning}
                    content = delta.get("content")
                    if content:
                        yield {"type": "content", "text": content}
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        entry = pending_tool_calls.setdefault(idx, {})
                        if tc.get("id"):
                            entry["id"] = tc["id"]
                        func = tc.get("function") or {}
                        if func.get("name"):
                            entry["name"] = func["name"]
                        if func.get("arguments"):
                            entry["arguments"] = entry.get("arguments", "") + func[
                                "arguments"
                            ]
                    finish_reason = choice.get("finish_reason")
                    if finish_reason == "tool_calls" or pending_tool_calls:
                        # Emit once the arguments look complete (tool_calls finished or stream ended).
                        calls = []
                        for idx in sorted(pending_tool_calls.keys()):
                            entry = pending_tool_calls[idx]
                            arguments = entry.get("arguments", "")
                            try:
                                parsed = json.loads(arguments) if arguments else {}
                            except json.JSONDecodeError:
                                parsed = {}
                            calls.append(
                                {
                                    "id": entry.get("id", f"tool_{idx}"),
                                    "name": entry.get("name", ""),
                                    "arguments": parsed,
                                }
                            )
                        if calls and (finish_reason == "tool_calls" or not content):
                            yield {"type": "tool_calls", "calls": calls}
                            pending_tool_calls = {}
                            if finish_reason == "tool_calls":
                                return
                if pending_tool_calls:
                    calls = []
                    for idx in sorted(pending_tool_calls.keys()):
                        entry = pending_tool_calls[idx]
                        arguments = entry.get("arguments", "")
                        try:
                            parsed = json.loads(arguments) if arguments else {}
                        except json.JSONDecodeError:
                            parsed = {}
                        calls.append(
                            {
                                "id": entry.get("id", f"tool_{idx}"),
                                "name": entry.get("name", ""),
                                "arguments": parsed,
                            }
                        )
                    if calls:
                        yield {"type": "tool_calls", "calls": calls}

    async def is_loaded(self, model: str) -> bool:
        return False

    async def warmup(self, model: str) -> int:
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=WARMUP_TIMEOUT) as client:
            response = await client.post(
                self._chat_path(),
                headers=self._auth_headers(),
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
