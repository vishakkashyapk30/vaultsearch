from pathlib import Path

import pytest

from app.acl import IdentityStore, can_access
from app.retrieval_core import Retriever

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def identity() -> IdentityStore:
    return IdentityStore.load(ROOT / "data" / "users_groups.json")


@pytest.fixture(scope="module")
def retriever(identity: IdentityStore) -> Retriever:
    return Retriever(ROOT / "indexes", identity, use_reranker=False)


@pytest.mark.parametrize(
    ("user_id", "query"),
    [
        ("user:asha", "What is the Q3 infrastructure budget?"),
        ("user:dmitri", "What is the engineering Atlas rollback plan?"),
        ("user:ines", "What is the Project Hawk offer range?"),
    ],
)
def test_every_result_is_authorized(
    retriever: Retriever,
    identity: IdentityStore,
    user_id: str,
    query: str,
) -> None:
    result = retriever.search(user_id, query, mode="hybrid")
    principals = identity.expand_principals(user_id)
    assert all(can_access(principals, item.allowed_principals) for item in result.chunks)


def test_finance_user_can_retrieve_budget(retriever: Retriever) -> None:
    result = retriever.search(
        "user:dmitri",
        "What is the Q3 infrastructure budget?",
        mode="hybrid",
    )
    assert any("1.2 million" in item.text for item in result.chunks)


def test_engineer_never_receives_finance_only_budget_evidence(retriever: Retriever) -> None:
    result = retriever.search(
        "user:asha",
        "What is the Q3 infrastructure budget?",
        mode="hybrid",
    )
    assert all("1.2 million" not in item.text for item in result.chunks)


def test_unknown_user_gets_no_results(retriever: Retriever) -> None:
    assert retriever.search("user:unknown", "onboarding", mode="hybrid").chunks == []
