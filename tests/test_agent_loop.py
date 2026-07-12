"""The iterative agent loop: refinement rounds, bounded budgets, and the
invariant that no amount of LLM-proposed tool use can widen access."""

import json

from app.acl import IdentityStore
from app.agents import Orchestrator, _strip_unauthorized_citations
from app.ollama_client import OllamaError

from test_tools import FakeRetriever, make_chunks, make_identity


class ScriptedLLM:
    """Stub OllamaClient: pops canned JSON responses; chat() returns text."""

    def __init__(self, json_responses: list[dict], text: str = "Answer [slack-1]."):
        self.json_responses = list(json_responses)
        self.text = text

    def chat_json(self, system: str, user: str) -> dict:
        if not self.json_responses:
            raise OllamaError("script exhausted")
        return self.json_responses.pop(0)

    def chat(self, system: str, user: str, **kwargs) -> str:
        return self.text


def make_orchestrator(llm, **kwargs) -> Orchestrator:
    identity = make_identity()
    retriever = FakeRetriever(make_chunks(), identity)
    return Orchestrator(retriever, identity, llm, use_critic=False, **kwargs)


def test_single_round_when_assessor_is_satisfied() -> None:
    llm = ScriptedLLM(
        [
            {"subqueries": ["pto days"]},
            {"sufficient": True, "reason": "evidence answers the question"},
        ]
    )
    result = make_orchestrator(llm).answer("user:eng", "How many PTO days?")
    assert len(result.trace["rounds"]) == 1
    assert result.trace["rounds"][0]["assessment"]["sufficient"] is True


def test_refinement_round_runs_new_query() -> None:
    llm = ScriptedLLM(
        [
            {"subqueries": ["vacation policy"]},  # matches nothing in fake corpus
            {
                "sufficient": False,
                "reason": "no evidence found",
                "tool_calls": [{"tool": "search", "args": {"query": "pto days"}}],
            },
            {"sufficient": True, "reason": "found it"},
        ]
    )
    result = make_orchestrator(llm).answer("user:eng", "How many PTO days?")
    rounds = result.trace["rounds"]
    assert len(rounds) == 2
    assert rounds[1]["type"] == "refine"
    assert rounds[1]["new_chunks"] == 1
    assert any(evidence["doc_id"] == "slack-1" for evidence in result.evidence)


def test_refinement_is_bounded_by_max_rounds() -> None:
    insufficient = {
        "sufficient": False,
        "reason": "keep going",
        "tool_calls": [{"tool": "search", "args": {"query": "different words"}}],
    }
    variants = [
        dict(insufficient, tool_calls=[{"tool": "search", "args": {"query": f"q{i}"}}])
        for i in range(10)
    ]
    llm = ScriptedLLM([{"subqueries": ["start"]}, *variants])
    result = make_orchestrator(llm, max_refine_rounds=2).answer(
        "user:eng", "anything"
    )
    assert len(result.trace["rounds"]) <= 3  # 1 plan + at most 2 refinements


def test_duplicate_refinement_queries_stop_the_loop() -> None:
    llm = ScriptedLLM(
        [
            {"subqueries": ["pto days"]},
            {
                "sufficient": False,
                "reason": "try again",
                "tool_calls": [{"tool": "search", "args": {"query": "pto days"}}],
            },
        ]
    )
    result = make_orchestrator(llm).answer("user:eng", "How many PTO days?")
    assert len(result.trace["rounds"]) == 1


def test_malformed_tool_calls_are_dropped() -> None:
    llm = ScriptedLLM(
        [
            {"subqueries": ["pto days"]},
            {
                "sufficient": False,
                "reason": "escalate",
                "tool_calls": [
                    {"tool": "read_all_documents", "args": {}},
                    {"tool": "search", "args": {"query": ""}},
                    "search everything",
                ],
            },
        ]
    )
    result = make_orchestrator(llm).answer("user:eng", "How many PTO days?")
    assert len(result.trace["rounds"]) == 1  # nothing valid to run


def test_no_refinement_can_widen_access() -> None:
    """Even an assessor that aggressively retries never surfaces finance-only
    content to an engineering user: every proposed search runs through the
    same ACL-filtered retriever and re-verification."""
    llm = ScriptedLLM(
        [
            {"subqueries": ["budget"]},
            {
                "sufficient": False,
                "reason": "look harder for the budget figure",
                "tool_calls": [
                    {"tool": "search", "args": {"query": "budget million amount"}},
                    {"tool": "search", "args": {"query": "1.2 million"}},
                ],
            },
            {"sufficient": False, "reason": "still nothing", "tool_calls": []},
        ]
    )
    result = make_orchestrator(llm).answer("user:eng", "What is the budget?")
    # The attacker-chosen query text legitimately echoes in the trace; what
    # must never appear is retrieved *content* from the finance-only chunk.
    surfaced = json.dumps(
        {"answer": result.answer, "evidence": result.evidence}, default=str
    )
    assert "1.2 million" not in surfaced
    assert all(evidence["doc_id"] != "drive-2" for evidence in result.evidence)
    assert result.trace["verification_rejections"] == []


def test_llm_failure_degrades_to_single_pass() -> None:
    llm = ScriptedLLM([])  # every chat_json raises OllamaError
    result = make_orchestrator(llm).answer("user:eng", "pto days")
    assert result.trace["planner_fallback"] is True
    assert len(result.trace["rounds"]) == 1
    assert result.trace["verified_chunks"] == 1


def test_lookup_person_feeds_directory_evidence() -> None:
    llm = ScriptedLLM(
        [
            {"subqueries": ["who is fred"]},
            {
                "sufficient": False,
                "reason": "need directory info",
                "tool_calls": [{"tool": "lookup_person", "args": {"name": "fred"}}],
            },
            {"sufficient": True, "reason": "found"},
        ],
        text="Fred Finance is in finance [directory].",
    )
    result = make_orchestrator(llm).answer("user:eng", "Which team is Fred on?")
    assert "directory" in result.citations
    assert any(evidence["doc_id"] == "directory" for evidence in result.evidence)


def test_sanitizer_filters_forged_ids_inside_grouped_citations() -> None:
    """A forged doc ID must not survive by hiding in a grouped citation."""
    allowed = {"drive-1", "drive-2"}
    answer = "Fact one [drive-1, finance-secret-001]. Fact two [drive-2,drive-1]."
    cleaned = _strip_unauthorized_citations(answer, allowed)
    assert "finance-secret-001" not in cleaned
    assert "[drive-1]" in cleaned and "[drive-2]" in cleaned


def test_sanitizer_drops_fully_forged_grouped_citation() -> None:
    cleaned = _strip_unauthorized_citations(
        "Claim [fake-1, fake-2].", {"drive-1"}
    )
    assert "fake" not in cleaned
    assert cleaned == "Claim."


def test_critic_verdict_is_advisory_and_recorded() -> None:
    llm = ScriptedLLM(
        [
            {"subqueries": ["pto days"]},
            {"sufficient": True, "reason": "ok"},
            {
                "verdict": "partially_grounded",
                "unsupported_claims": ["made-up detail"],
            },
        ]
    )
    identity = make_identity()
    retriever = FakeRetriever(make_chunks(), identity)
    orchestrator = Orchestrator(retriever, identity, llm, use_critic=True)
    result = orchestrator.answer("user:eng", "How many PTO days?")
    assert result.trace["critic"]["verdict"] == "partially_grounded"
    assert result.trace["critic"]["unsupported_claims"] == ["made-up detail"]
    # Advisory only: the answer itself is untouched by the critic.
    assert result.answer.startswith("Answer")
