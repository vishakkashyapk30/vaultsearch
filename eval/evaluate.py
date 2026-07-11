"""Offline retrieval quality, permission leakage, and latency evaluation."""

from __future__ import annotations

import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.acl import IdentityStore, can_access
from app.retrieval_core import Retriever

ROOT = Path(__file__).resolve().parent.parent

# query, title fragment identifying relevant documents, authorized user
CASES = [
    ("How will Project Atlas database migration roll back?", "Project Atlas", "user:asha"),
    ("What is the p95 search latency target?", "Search latency", "user:ben"),
    ("What is the Q3 infrastructure budget cap?", "Q3 budget", "user:dmitri"),
    ("What company is Project Hawk evaluating?", "Project Hawk", "user:elena"),
    ("What is the L4 engineer salary band?", "Compensation band", "user:farid"),
    ("What caused the June 12 payments outage?", "SEV1 payments", "user:chitra"),
    ("How does the onboarding buddy program work?", "New hire onboarding", "user:ines"),
    ("How many paid time off days are offered?", "Paid time off", "user:ines"),
    ("Where and when is the annual offsite?", "Company offsite", "user:ines"),
    ("Which vendors need security remediation?", "Vendor security", "user:asha"),
    ("How much did hybrid retrieval improve NDCG?", "Search ranking", "user:kavya"),
    ("What happens during the office network refresh?", "Office network", "user:ines"),
]

# Questions deliberately assigned to users without the required ACL.
ADVERSARIAL = [
    ("user:asha", "What is the Q3 infrastructure budget cap?", "1.2 million"),
    ("user:asha", "What is the L4 engineer salary band?", "145 to 175"),
    ("user:dmitri", "What is the Atlas rollback plan?", "repoint the connection"),
    ("user:ines", "What is the Project Hawk offer range?", "45 to 60 million"),
    ("user:ines", "What caused the payments outage?", "misconfigured connection"),
]


def relevant_ids(retriever: Retriever, title_fragment: str) -> set[str]:
    needle = title_fragment.lower()
    return {
        chunk["chunk_id"]
        for chunk in retriever.chunks
        if needle in chunk["title"].lower()
    }


def dcg(binary_relevance: list[int]) -> float:
    return sum(rel / math.log2(rank + 2) for rank, rel in enumerate(binary_relevance))


def ndcg_at_10(result_ids: list[str], relevant: set[str]) -> float:
    rel = [int(result_id in relevant) for result_id in result_ids[:10]]
    ideal = [1] * min(10, len(relevant))
    return dcg(rel) / dcg(ideal) if ideal else 0.0


def reciprocal_rank(result_ids: list[str], relevant: set[str]) -> float:
    for rank, result_id in enumerate(result_ids, start=1):
        if result_id in relevant:
            return 1.0 / rank
    return 0.0


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


def main() -> None:
    identity = IdentityStore.load(ROOT / "data" / "users_groups.json")
    retriever = Retriever(ROOT / "indexes", identity, use_reranker=True)
    modes = ["bm25", "vector", "hybrid", "hybrid+rerank"]
    rows: list[tuple[str, float, float, float, float]] = []

    for mode in modes:
        ndcgs: list[float] = []
        mrrs: list[float] = []
        latencies: list[float] = []
        for query, title, user in CASES:
            result = retriever.search(user, query, top_n=10, mode=mode)
            ids = [chunk.chunk_id for chunk in result.chunks]
            relevant = relevant_ids(retriever, title)
            ndcgs.append(ndcg_at_10(ids, relevant))
            mrrs.append(reciprocal_rank(ids, relevant))
            latencies.append(result.stage_latency_ms["total"])
        rows.append(
            (
                mode,
                statistics.mean(ndcgs),
                statistics.mean(mrrs),
                statistics.median(latencies),
                percentile(latencies, 0.95),
            )
        )

    attempts = 0
    leaks = 0
    authorization_failures = 0
    # Repeat the 5 distinct probes across retrieval modes and top-k settings:
    # 5 * 4 * 5 = 100 adversarial retrieval attempts.
    for user, query, secret in ADVERSARIAL:
        principals = identity.expand_principals(user)
        for mode in modes:
            for top_k in (10, 20, 30, 40, 50):
                result = retriever.search(user, query, top_k=top_k, top_n=10, mode=mode)
                attempts += 1
                text = " ".join(chunk.text.lower() for chunk in result.chunks)
                if secret.lower() in text:
                    leaks += 1
                if any(not can_access(principals, c.allowed_principals) for c in result.chunks):
                    authorization_failures += 1

    report = [
        "# VaultSearch Evaluation Report",
        "",
        "## Retrieval quality and latency",
        "",
        "| Mode | NDCG@10 | MRR | p50 latency (ms) | p95 latency (ms) |",
        "|---|---:|---:|---:|---:|",
    ]
    report.extend(
        f"| {mode} | {ndcg:.3f} | {mrr:.3f} | {p50:.1f} | {p95:.1f} |"
        for mode, ndcg, mrr, p50, p95 in rows
    )
    report.extend(
        [
            "",
            "## Permission safety",
            "",
            f"- Adversarial retrieval attempts: {attempts}",
            f"- Restricted-fact leaks: {leaks}",
            f"- Unauthorized chunks returned: {authorization_failures}",
            f"- Permission leakage rate: {100 * leaks / attempts:.2f}%",
            "",
            "All queries use retrieval-time ACL pre-filtering. The leakage metric also",
            "searches returned chunk text for a distinctive restricted fact.",
        ]
    )
    output = ROOT / "reports" / "evaluation_report.md"
    output.parent.mkdir(exist_ok=True)
    output.write_text("\n".join(report) + "\n")
    print("\n".join(report))


if __name__ == "__main__":
    main()
