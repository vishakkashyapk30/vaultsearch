"""Query planning, permission verification, and cited answer synthesis."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .acl import IdentityStore, can_access
from .ollama_client import OllamaClient, OllamaError
from .retrieval_core import RetrievedChunk, Retriever


_CITATION_RE = re.compile(r"\s*\[([A-Za-z0-9_-]+)\]")


def _strip_unauthorized_citations(answer: str, allowed_doc_ids: set[str]) -> str:
    """Remove any citation the model emitted that isn't verified evidence.

    Also cleans up the punctuation left behind (e.g. dangling ", ,") so the
    sanitized text reads naturally.
    """
    answer = _CITATION_RE.sub(
        lambda m: m.group(0) if m.group(1) in allowed_doc_ids else "",
        answer,
    )
    # Collapse artifacts like "[a], , [b]" or " ,  ," left by removed citations.
    answer = re.sub(r"(?:\s*,)+(\s*,)", r"\1", answer)
    answer = re.sub(r"\s+,", ",", answer)
    answer = re.sub(r",\s*,+", ",", answer)
    answer = re.sub(r"\(\s*,\s*", "(", answer)
    answer = re.sub(r"\s*,\s*\)", ")", answer)
    answer = re.sub(r"\s{2,}", " ", answer)
    answer = re.sub(r"\s+([.)])", r"\1", answer)
    return answer.strip()


@dataclass
class QueryPlan:
    subqueries: list[str]
    used_fallback: bool = False


@dataclass
class AnswerResult:
    answer: str
    citations: list[str]
    evidence: list[dict]
    trace: dict
    latency_ms: dict[str, float] = field(default_factory=dict)


class QueryPlanner:
    def __init__(self, llm: OllamaClient):
        self.llm = llm

    def plan(self, question: str) -> QueryPlan:
        system = (
            "You plan retrieval queries for enterprise search. Return JSON only: "
            '{"subqueries":["..."]}. Use one query for a focused question and at '
            "most four concise, standalone queries for a comparison or multi-part "
            "question. Never include a user identity or permissions in a query."
        )
        try:
            value = self.llm.chat_json(system, question)
            queries = value.get("subqueries", [])
            if not isinstance(queries, list):
                raise ValueError("subqueries is not a list")
            clean = [str(query).strip() for query in queries if str(query).strip()][
                :4
            ]
            if clean:
                return QueryPlan(clean)
        except (OllamaError, ValueError):
            pass
        return QueryPlan([question.strip()], used_fallback=True)


class PermissionVerifier:
    """Independently verify authorization after retrieval."""

    def __init__(self, identity: IdentityStore):
        self.identity = identity

    def verify(
        self,
        user_id: str,
        chunks: list[RetrievedChunk],
    ) -> tuple[list[RetrievedChunk], list[str]]:
        principals = self.identity.expand_principals(user_id)
        verified: list[RetrievedChunk] = []
        rejected: list[str] = []
        for chunk in chunks:
            if can_access(principals, chunk.allowed_principals):
                verified.append(chunk)
            else:
                rejected.append(chunk.chunk_id)
        return verified, rejected


class AnswerSynthesizer:
    def __init__(self, llm: OllamaClient):
        self.llm = llm

    def synthesize(self, question: str, chunks: list[RetrievedChunk]) -> str:
        if not chunks:
            return "I could not find permitted evidence that answers this question."
        evidence = "\n\n".join(
            f"[{chunk.doc_id}] {chunk.title}\n{chunk.text}" for chunk in chunks
        )
        system = (
            "Answer only from the supplied evidence. Cite every factual claim with "
            "one or more document IDs exactly like [drive-001]. If evidence is "
            "insufficient, say so. Do not infer confidential details or mention ACLs. "
            "Be concise."
        )
        try:
            return self.llm.chat(
                system,
                f"Question: {question}\n\nEvidence:\n{evidence}",
            )
        except OllamaError:
            sources = ", ".join(f"[{chunk.doc_id}]" for chunk in chunks[:3])
            return (
                f"Relevant permitted evidence was found in {sources}, "
                "but answer synthesis is unavailable."
            )


class Orchestrator:
    def __init__(
        self,
        retriever: Retriever,
        identity: IdentityStore,
        llm: OllamaClient,
    ):
        self.retriever = retriever
        self.planner = QueryPlanner(llm)
        self.verifier = PermissionVerifier(identity)
        self.synthesizer = AnswerSynthesizer(llm)

    def answer(
        self,
        user_id: str,
        question: str,
        top_n: int = 6,
    ) -> AnswerResult:
        total_start = time.perf_counter()

        start = time.perf_counter()
        plan = self.planner.plan(question)
        planning_ms = (time.perf_counter() - start) * 1000

        retrieval_traces: list[dict] = []
        unique: dict[str, RetrievedChunk] = {}
        for subquery in plan.subqueries:
            result = self.retriever.search(user_id, subquery, top_n=top_n)
            retrieval_traces.append(
                {
                    "query": subquery,
                    "returned": len(result.chunks),
                    "allowed_candidates": result.candidates_allowed,
                    "total_candidates": result.candidates_total,
                    "latency_ms": result.stage_latency_ms,
                }
            )
            for chunk in result.chunks:
                current = unique.get(chunk.chunk_id)
                if current is None or chunk.score > current.score:
                    unique[chunk.chunk_id] = chunk

        start = time.perf_counter()
        verified, rejected = self.verifier.verify(user_id, list(unique.values()))
        verification_ms = (time.perf_counter() - start) * 1000

        start = time.perf_counter()
        answer = self.synthesizer.synthesize(question, verified)
        synthesis_ms = (time.perf_counter() - start) * 1000

        allowed_doc_ids = {chunk.doc_id for chunk in verified}
        answer = _strip_unauthorized_citations(answer, allowed_doc_ids)
        citations = sorted(
            set(re.findall(r"\[([A-Za-z0-9_-]+)\]", answer))
        )
        evidence = [
            {
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "source": chunk.source,
                "title": chunk.title,
                "text": chunk.text,
                "score": round(chunk.score, 4),
                "allowed_principals": chunk.allowed_principals,
                "cited": chunk.doc_id in citations,
            }
            for chunk in sorted(verified, key=lambda c: -c.score)
        ]
        latency = {
            "planning": planning_ms,
            "verification": verification_ms,
            "synthesis": synthesis_ms,
            "total": (time.perf_counter() - total_start) * 1000,
        }
        trace = {
            "subqueries": plan.subqueries,
            "planner_fallback": plan.used_fallback,
            "retrieval": retrieval_traces,
            "verified_chunks": len(verified),
            "verification_rejections": rejected,
        }
        return AnswerResult(answer, citations, evidence, trace, latency)
