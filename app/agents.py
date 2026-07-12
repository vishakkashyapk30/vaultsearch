"""The agent layer: planning, iterative tool use, verification, synthesis.

The trust boundary is unchanged from the single-pass design: the LLM decides
*what to look for* (planning, sufficiency assessment) and *how to phrase the
answer* (synthesis); deterministic code decides *what the user may read*
(ACL pre-filter + independent re-verification) and *which citations survive*
(sanitizer). The iterative loop gives the model more autonomy over retrieval
quality while giving it zero additional authority over access.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

from .acl import IdentityStore, can_access
from .ollama_client import OllamaClient, OllamaError
from .retrieval_core import RetrievedChunk, Retriever
from .tools import TOOL_SPECS, Toolbox, validate_tool_call

DIRECTORY_DOC_ID = "directory"

# Matches single citations [drive-001] and grouped ones [drive-001, ticket-02];
# the group form must be caught too or a forged ID could hide inside a list.
_CITATION_RE = re.compile(r"\s*\[([A-Za-z0-9_-]+(?:\s*,\s*[A-Za-z0-9_-]+)*)\]")


def _strip_unauthorized_citations(answer: str, allowed_doc_ids: set[str]) -> str:
    """Remove any citation the model emitted that isn't verified evidence.

    Grouped citations are split, filtered ID by ID, and re-emitted as
    individual brackets. Also cleans up the punctuation left behind
    (e.g. dangling ", ,") so the sanitized text reads naturally.
    """

    def _filter(match: re.Match) -> str:
        ids = [part.strip() for part in match.group(1).split(",")]
        kept = [doc_id for doc_id in ids if doc_id in allowed_doc_ids]
        if not kept:
            return ""
        return " " + ", ".join(f"[{doc_id}]" for doc_id in kept)

    answer = _CITATION_RE.sub(_filter, answer)
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
class Assessment:
    sufficient: bool
    reason: str
    tool_calls: list[tuple[str, dict]]


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


class EvidenceAssessor:
    """LLM judgment of whether gathered evidence answers the question.

    A wrong answer here costs at most one wasted round or one missed
    refinement, never safety: proposed tool calls are validated by
    deterministic code and executed through the same permission-gated
    Toolbox as everything else. If the LLM is down or returns malformed
    JSON, assessment returns None and the loop simply stops.
    """

    MAX_CALLS_PER_ROUND = 3

    def __init__(self, llm: OllamaClient):
        self.llm = llm

    def assess(
        self,
        question: str,
        chunks: list[RetrievedChunk],
        tried_queries: list[str],
    ) -> Assessment | None:
        tools = "; ".join(TOOL_SPECS.values())
        system = (
            "You review evidence gathered so far for an enterprise search "
            "question and decide whether it is sufficient to answer. Return "
            'JSON only: {"sufficient": true|false, "reason": "...", '
            '"tool_calls": [{"tool": "search", "args": {"query": "..."}}]}. '
            f"Available tools: {tools}. Propose at most "
            f"{self.MAX_CALLS_PER_ROUND} calls and only when the current "
            "evidence cannot answer the question; prefer rephrasing with "
            "different keywords than the queries already tried. Never include "
            "a user identity or permissions in any argument."
        )
        evidence = "\n".join(
            f"- [{chunk.doc_id}] {chunk.title}: {chunk.text[:160]}"
            for chunk in chunks[:10]
        ) or "(no evidence retrieved yet)"
        tried = "\n".join(f"- {query}" for query in tried_queries)
        user = (
            f"Question: {question}\n\nQueries already tried:\n{tried}\n\n"
            f"Evidence gathered:\n{evidence}"
        )
        try:
            value = self.llm.chat_json(system, user)
            sufficient = bool(value.get("sufficient", True))
            reason = str(value.get("reason", "")).strip()[:300]
            raw_calls = value.get("tool_calls", [])
            if not isinstance(raw_calls, list):
                raw_calls = []
            calls: list[tuple[str, dict]] = []
            for raw in raw_calls[: self.MAX_CALLS_PER_ROUND]:
                validated = validate_tool_call(raw)
                if validated is not None:
                    calls.append(validated)
            return Assessment(sufficient=sufficient, reason=reason, tool_calls=calls)
        except OllamaError:
            return None


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

    def synthesize(
        self,
        question: str,
        chunks: list[RetrievedChunk],
        directory_facts: list[str] | None = None,
    ) -> str:
        if not chunks and not directory_facts:
            return "I could not find permitted evidence that answers this question."
        blocks = [
            f"[{chunk.doc_id}] {chunk.title}\n{chunk.text}" for chunk in chunks
        ]
        if directory_facts:
            facts = "\n".join(directory_facts)
            blocks.append(f"[{DIRECTORY_DOC_ID}] Company directory\n{facts}")
        evidence = "\n\n".join(blocks)
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


class GroundednessCritic:
    """Advisory LLM review of whether the answer is supported by evidence.

    The verdict is surfaced in the trace and UI but never enforces anything;
    the deterministic sanitizer remains the enforcement layer for citations.
    """

    def __init__(self, llm: OllamaClient):
        self.llm = llm

    def review(
        self,
        question: str,
        answer: str,
        chunks: list[RetrievedChunk],
    ) -> dict:
        system = (
            "You check whether an answer is fully supported by the supplied "
            "evidence. Return JSON only: "
            '{"verdict": "grounded"|"partially_grounded"|"ungrounded", '
            '"unsupported_claims": ["..."]}. A claim is unsupported if no '
            "evidence block states it. Ignore citation formatting."
        )
        evidence = "\n\n".join(
            f"[{chunk.doc_id}] {chunk.title}\n{chunk.text}" for chunk in chunks
        )
        user = f"Question: {question}\n\nAnswer:\n{answer}\n\nEvidence:\n{evidence}"
        try:
            value = self.llm.chat_json(system, user)
            verdict = str(value.get("verdict", "")).strip()
            if verdict not in ("grounded", "partially_grounded", "ungrounded"):
                return {"verdict": "unavailable"}
            claims = value.get("unsupported_claims", [])
            if not isinstance(claims, list):
                claims = []
            return {
                "verdict": verdict,
                "unsupported_claims": [str(claim)[:200] for claim in claims[:5]],
            }
        except OllamaError:
            return {"verdict": "unavailable"}


class Orchestrator:
    def __init__(
        self,
        retriever: Retriever,
        identity: IdentityStore,
        llm: OllamaClient,
        max_refine_rounds: int | None = None,
        use_critic: bool | None = None,
    ):
        self.retriever = retriever
        self.identity = identity
        self.planner = QueryPlanner(llm)
        self.assessor = EvidenceAssessor(llm)
        self.verifier = PermissionVerifier(identity)
        self.synthesizer = AnswerSynthesizer(llm)
        self.critic = GroundednessCritic(llm)
        self.max_refine_rounds = (
            max_refine_rounds
            if max_refine_rounds is not None
            else int(os.getenv("AGENT_MAX_ROUNDS", "2"))
        )
        self.use_critic = (
            use_critic
            if use_critic is not None
            else os.getenv("USE_CRITIC", "true").lower() == "true"
        )

    @staticmethod
    def _merge(unique: dict[str, RetrievedChunk], chunks: list[RetrievedChunk]) -> int:
        new = 0
        for chunk in chunks:
            current = unique.get(chunk.chunk_id)
            if current is None:
                new += 1
            if current is None or chunk.score > current.score:
                unique[chunk.chunk_id] = chunk
        return new

    @staticmethod
    def _directory_facts(toolbox: Toolbox) -> list[str]:
        facts: list[str] = []
        for execution in toolbox.calls:
            if execution.error:
                continue
            if execution.tool == "lookup_person":
                for match in execution.payload.get("matches", []):
                    groups = ", ".join(
                        group.replace("group:", "") for group in match["groups"]
                    )
                    facts.append(
                        f"{match['name']} ({match['user_id']}) is in: {groups}"
                    )
            elif execution.tool == "list_my_sources":
                sources = ", ".join(
                    f"{name}: {count}"
                    for name, count in execution.payload.get("sources", {}).items()
                )
                facts.append(f"Content accessible to you per source: {sources}")
        return facts

    def answer(
        self,
        user_id: str,
        question: str,
        top_n: int = 6,
    ) -> AnswerResult:
        total_start = time.perf_counter()
        toolbox = Toolbox(user_id, self.retriever, self.identity)

        start = time.perf_counter()
        plan = self.planner.plan(question)
        planning_ms = (time.perf_counter() - start) * 1000

        unique: dict[str, RetrievedChunk] = {}
        tried_queries: list[str] = []
        rounds: list[dict] = []

        def run_searches(calls: list[tuple[str, dict]]) -> tuple[list[dict], int]:
            entries: list[dict] = []
            new_chunks = 0
            for tool, args in calls:
                execution = toolbox.execute(tool, args, top_n=top_n)
                entries.append(execution.trace_entry())
                if tool == "search" and not execution.error:
                    tried_queries.append(args["query"])
                    new_chunks += self._merge(unique, execution.chunks)
            return entries, new_chunks

        entries, new_chunks = run_searches(
            [("search", {"query": query}) for query in plan.subqueries]
        )
        rounds.append(
            {
                "round": 1,
                "type": "plan",
                "reason": "initial retrieval plan",
                "tool_calls": entries,
                "new_chunks": new_chunks,
            }
        )

        assessment_ms = 0.0
        for refine in range(self.max_refine_rounds):
            start = time.perf_counter()
            interim, _ = self.verifier.verify(user_id, list(unique.values()))
            assessment = self.assessor.assess(
                question,
                sorted(interim, key=lambda chunk: -chunk.score),
                tried_queries,
            )
            assessment_ms += (time.perf_counter() - start) * 1000
            if assessment is None or assessment.sufficient:
                if assessment is not None:
                    rounds[-1]["assessment"] = {
                        "sufficient": True,
                        "reason": assessment.reason,
                    }
                break
            calls = [
                (tool, args)
                for tool, args in assessment.tool_calls
                if not (tool == "search" and args["query"] in tried_queries)
            ]
            if not calls:
                rounds[-1]["assessment"] = {
                    "sufficient": False,
                    "reason": assessment.reason,
                }
                break
            entries, new_chunks = run_searches(calls)
            rounds.append(
                {
                    "round": refine + 2,
                    "type": "refine",
                    "reason": assessment.reason,
                    "tool_calls": entries,
                    "new_chunks": new_chunks,
                }
            )

        start = time.perf_counter()
        verified, rejected = self.verifier.verify(user_id, list(unique.values()))
        verification_ms = (time.perf_counter() - start) * 1000

        verified = sorted(verified, key=lambda chunk: -chunk.score)
        directory_facts = self._directory_facts(toolbox)

        start = time.perf_counter()
        answer = self.synthesizer.synthesize(question, verified, directory_facts)
        synthesis_ms = (time.perf_counter() - start) * 1000

        allowed_doc_ids = {chunk.doc_id for chunk in verified}
        if directory_facts:
            allowed_doc_ids.add(DIRECTORY_DOC_ID)
        answer = _strip_unauthorized_citations(answer, allowed_doc_ids)
        citations = sorted(set(re.findall(r"\[([A-Za-z0-9_-]+)\]", answer)))

        critic_ms = 0.0
        critic_result = {"verdict": "skipped"}
        if self.use_critic and verified:
            start = time.perf_counter()
            critic_result = self.critic.review(question, answer, verified)
            critic_ms = (time.perf_counter() - start) * 1000

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
            for chunk in verified
        ]
        if directory_facts:
            evidence.append(
                {
                    "chunk_id": f"{DIRECTORY_DOC_ID}#0",
                    "doc_id": DIRECTORY_DOC_ID,
                    "source": "directory",
                    "title": "Company directory",
                    "text": "\n".join(directory_facts),
                    "score": 0.0,
                    "allowed_principals": ["group:all-staff"],
                    "cited": DIRECTORY_DOC_ID in citations,
                }
            )

        retrieval_traces = [
            execution.trace_entry() | {"query": execution.args.get("query", "")}
            for execution in toolbox.calls
            if execution.tool == "search"
        ]
        latency = {
            "planning": planning_ms,
            "assessment": assessment_ms,
            "verification": verification_ms,
            "synthesis": synthesis_ms,
            "critic": critic_ms,
            "total": (time.perf_counter() - total_start) * 1000,
        }
        trace = {
            "subqueries": plan.subqueries,
            "planner_fallback": plan.used_fallback,
            "retrieval": retrieval_traces,
            "rounds": rounds,
            "tool_calls": [execution.trace_entry() for execution in toolbox.calls],
            "verified_chunks": len(verified),
            "verification_rejections": rejected,
            "critic": critic_result,
        }
        return AnswerResult(answer, citations, evidence, trace, latency)
