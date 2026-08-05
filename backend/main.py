from contextlib import asynccontextmanager
import os
import shutil
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import db
from chat.router import router as chat_router
from memory.router import router as memory_router
from mcp_client import manager as mcp_manager
from providers.router import router as providers_router
from tools import router as tools_router

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))

DEFAULT_SOUL = """# Soul

You are a local-first personal assistant running entirely on the user's own machine.
You are direct, concise, and honest — you say when you don't know.
You respect privacy above all: nothing leaves this machine unless the user asks.
You remember what matters and forget what doesn't.
"""


def ensure_soul_file() -> None:
    soul = DATA_DIR / "soul.md"
    if soul.exists():
        return
    example = DATA_DIR / "soul.example.md"
    if example.exists():
        shutil.copyfile(example, soul)
    else:
        soul.write_text(DEFAULT_SOUL, encoding="utf-8")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_soul_file()
    await db.init_pool()
    await db.run_ddl()
    await db.seed()
    await mcp_manager.start()
    yield
    await mcp_manager.stop()
    await db.close_pool()


app = FastAPI(title="local-ai-hub", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(providers_router)
app.include_router(chat_router)
app.include_router(memory_router)
app.include_router(tools_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/info")
async def info():
    return {"name": "local-ai-hub", "phase": 4, "db": await db.check()}


class EchoIn(BaseModel):
    text: str


@app.post("/api/echo")
async def echo(body: EchoIn):
    return {"text": body.text}
