"""API HTTP. Ejecuta: uvicorn app.main:app --reload"""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .agent import Agent
from .domain import CATALOG, Agenda
from .llm import llm_from_env

STATIC = Path(__file__).parent / "static"


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ChatOut(BaseModel):
    session_id: str
    reply: str
    action: str
    trace: list[dict]
    data: dict


def create_app(agent: Agent | None = None) -> FastAPI:
    agent = agent or Agent(llm_from_env(), Agenda())
    app = FastAPI(title="Guardrailed Agent", version="1.0.0")
    app.state.agent = agent

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "llm": agent.llm.name}

    @app.get("/services")
    def services() -> list[dict]:
        return [s.__dict__ for s in CATALOG.values()]

    @app.post("/chat", response_model=ChatOut)
    def chat(body: ChatIn) -> ChatOut:
        sid = body.session_id or uuid.uuid4().hex[:12]
        r = agent.handle(sid, body.message)
        return ChatOut(session_id=sid, reply=r.reply, action=r.action, trace=r.trace, data=r.data)

    return app


app = create_app()
