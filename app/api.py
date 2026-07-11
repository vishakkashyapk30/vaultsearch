"""FastAPI entrypoint for VaultSearch."""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .acl import IdentityStore
from .agents import Orchestrator
from .ollama_client import OllamaClient
from .retrieval_core import Retriever

ROOT = Path(__file__).resolve().parent.parent


def _audit_logger() -> logging.Logger:
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    logger = logging.getLogger("vaultsearch.audit")
    if not logger.handlers:
        handler = logging.FileHandler(logs / "audit.jsonl")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    identity = IdentityStore.load(ROOT / "data" / "users_groups.json")
    retriever = Retriever(
        ROOT / "indexes",
        identity,
        use_reranker=os.getenv("USE_RERANKER", "true").lower() == "true",
    )
    app.state.identity = identity
    app.state.orchestrator = Orchestrator(
        retriever,
        identity,
        OllamaClient(),
    )
    app.state.audit = _audit_logger()
    yield


app = FastAPI(
    title="VaultSearch",
    version="0.1.0",
    description="Local permission-aware hybrid enterprise search",
    lifespan=lifespan,
)


class AskRequest(BaseModel):
    user_id: str = Field(examples=["user:asha"])
    question: str = Field(min_length=2, max_length=2000)
    top_n: int = Field(default=6, ge=1, le=20)


class AskResponse(BaseModel):
    answer: str
    citations: list[str]
    trace: dict
    latency_ms: dict[str, float]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest, request: Request) -> AskResponse:
    identity: IdentityStore = request.app.state.identity
    if not identity.known_user(payload.user_id):
        raise HTTPException(status_code=404, detail="Unknown user")

    result = request.app.state.orchestrator.answer(
        payload.user_id,
        payload.question,
        payload.top_n,
    )
    audit_event = {
        "event": "ask",
        "user_id": payload.user_id,
        "question": payload.question,
        "citations": result.citations,
        "trace": result.trace,
        "latency_ms": result.latency_ms,
    }
    request.app.state.audit.info(
        json.dumps(audit_event, separators=(",", ":"))
    )
    return AskResponse(**result.__dict__)
