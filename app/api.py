"""FastAPI entrypoint: JSON API plus the served web interface."""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .acl import IdentityStore, can_access
from .agents import Orchestrator
from .cloud_audit import make_audit_sink
from .ollama_client import OllamaClient
from .retrieval_core import Retriever

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

RETRIEVAL_MODES = ["bm25", "vector", "hybrid", "hybrid+rerank"]


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
    app.state.retriever = retriever
    app.state.orchestrator = Orchestrator(retriever, identity, OllamaClient())
    app.state.audit = _audit_logger()
    app.state.audit_sink = make_audit_sink()
    yield


app = FastAPI(
    title="VaultSearch",
    version="1.0.0",
    description="A secure retrieval boundary for RAG: no answer ever surfaces data the user cannot see.",
    lifespan=lifespan,
)


class AskRequest(BaseModel):
    user_id: str = Field(examples=["user:asha"])
    question: str = Field(min_length=2, max_length=2000)
    top_n: int = Field(default=6, ge=1, le=20)


class AskResponse(BaseModel):
    answer: str
    citations: list[str]
    evidence: list[dict]
    trace: dict
    latency_ms: dict[str, float]


class SearchRequest(BaseModel):
    user_id: str
    query: str = Field(min_length=2, max_length=2000)
    top_n: int = Field(default=6, ge=1, le=20)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/users")
def users(request: Request) -> dict:
    identity: IdentityStore = request.app.state.identity
    retriever: Retriever = request.app.state.retriever
    directory = identity.directory()
    for record in directory:
        principals = identity.expand_principals(record["user_id"])
        visible = sum(
            1
            for chunk in retriever.chunks
            if can_access(principals, chunk["allowed_principals"])
        )
        record["visible_chunks"] = visible
    return {"users": directory, "total_chunks": len(retriever.chunks)}


@app.post("/api/search")
def search(payload: SearchRequest, request: Request) -> dict:
    """Run every retrieval mode so the UI can compare them side by side."""
    identity: IdentityStore = request.app.state.identity
    retriever: Retriever = request.app.state.retriever
    if not identity.known_user(payload.user_id):
        raise HTTPException(status_code=404, detail="Unknown user")

    modes: dict[str, dict] = {}
    allowed = 0
    for mode in RETRIEVAL_MODES:
        result = retriever.search(
            payload.user_id, payload.query, top_n=payload.top_n, mode=mode
        )
        allowed = result.candidates_allowed
        modes[mode] = {
            "results": [
                {
                    "doc_id": chunk.doc_id,
                    "source": chunk.source,
                    "title": chunk.title,
                    "text": chunk.text,
                    "score": round(chunk.score, 4),
                }
                for chunk in result.chunks
            ],
            "latency_ms": result.stage_latency_ms,
        }
    return {
        "modes": modes,
        "visible_chunks": allowed,
        "total_chunks": len(retriever.chunks),
    }


@app.post("/api/ask", response_model=AskResponse)
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
    request.app.state.audit.info(json.dumps(audit_event, separators=(",", ":")))
    if request.app.state.audit_sink is not None:
        request.app.state.audit_sink.write(audit_event)
    return AskResponse(**result.__dict__)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
