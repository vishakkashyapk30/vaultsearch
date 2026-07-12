"""Permission-gated tools shared by the orchestrator and the MCP server.

A Toolbox is bound to one user identity at construction time. The identity
travels out-of-band: no tool accepts a user_id argument, so a language model
choosing tools can never choose *whose* permissions apply. Every tool enforces
authorization internally through the same `can_access` predicate as the rest
of the system, and every execution is recorded in a call log for the trace.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .acl import IdentityStore, can_access
from .retrieval_core import RetrievedChunk, Retriever

MAX_QUERY_CHARS = 500

TOOL_SPECS: dict[str, str] = {
    "search": "search(query): permission-filtered hybrid retrieval over the corpus",
    "lookup_person": "lookup_person(name): find a person in the company directory",
    "list_my_sources": "list_my_sources(): per-source counts of content you can access",
}


@dataclass
class ToolExecution:
    tool: str
    args: dict
    payload: dict = field(default_factory=dict)
    chunks: list[RetrievedChunk] = field(default_factory=list)
    error: str | None = None
    latency_ms: float = 0.0

    def trace_entry(self) -> dict:
        entry = {"tool": self.tool, "args": self.args, "latency_ms": round(self.latency_ms, 2)}
        if self.error:
            entry["error"] = self.error
        else:
            entry.update(self.payload)
        return entry


def validate_tool_call(call: object) -> tuple[str, dict] | None:
    """Return (tool, args) if the model-proposed call is well-formed, else None."""
    if not isinstance(call, dict):
        return None
    tool = call.get("tool")
    args = call.get("args", {})
    if tool not in TOOL_SPECS or not isinstance(args, dict):
        return None
    if tool == "search":
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return None
        return tool, {"query": query.strip()[:MAX_QUERY_CHARS]}
    if tool == "lookup_person":
        name = args.get("name")
        if not isinstance(name, str) or not name.strip():
            return None
        return tool, {"name": name.strip()[:100]}
    return tool, {}


class Toolbox:
    """Tools bound to a single user identity, with a per-instance call log."""

    def __init__(self, user_id: str, retriever: Retriever, identity: IdentityStore):
        self.user_id = user_id
        self.retriever = retriever
        self.identity = identity
        self.calls: list[ToolExecution] = []

    def execute(self, tool: str, args: dict, top_n: int = 6) -> ToolExecution:
        start = time.perf_counter()
        execution = ToolExecution(tool=tool, args=dict(args))
        try:
            if tool == "search":
                result = self.retriever.search(self.user_id, args["query"], top_n=top_n)
                execution.chunks = result.chunks
                execution.payload = {
                    "returned": len(result.chunks),
                    "allowed_candidates": result.candidates_allowed,
                    "total_candidates": result.candidates_total,
                    "stage_latency_ms": result.stage_latency_ms,
                }
            elif tool == "lookup_person":
                execution.payload = {"matches": self.lookup_person(args["name"])}
            elif tool == "list_my_sources":
                execution.payload = self.list_my_sources()
            else:
                execution.error = f"unknown tool: {tool}"
        except KeyError as exc:
            execution.error = f"missing argument: {exc}"
        execution.latency_ms = (time.perf_counter() - start) * 1000
        self.calls.append(execution)
        return execution

    def lookup_person(self, name: str) -> list[dict]:
        """Directory lookup: org-public metadata (names and group membership).

        Never exposes what documents a person can read; visibility counts are
        only reported for the calling identity via list_my_sources.
        """
        needle = name.strip().lower()
        return [
            record
            for record in self.identity.directory()
            if needle in record["name"].lower() or needle in record["user_id"].lower()
        ][:5]

    def list_my_sources(self) -> dict:
        principals = self.identity.expand_principals(self.user_id)
        per_source: dict[str, int] = {}
        visible = 0
        for chunk in self.retriever.chunks:
            if can_access(principals, chunk["allowed_principals"]):
                visible += 1
                per_source[chunk["source"]] = per_source.get(chunk["source"], 0) + 1
        return {
            "sources": dict(sorted(per_source.items())),
            "visible_chunks": visible,
            "total_chunks": len(self.retriever.chunks),
        }
