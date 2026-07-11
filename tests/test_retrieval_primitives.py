from app.retrieval_core import rrf_fuse, tokenize


def test_tokenize_normalizes_and_removes_punctuation() -> None:
    assert tokenize("Q3 Budget: $1.2M!") == ["q3", "budget", "1", "2m"]


def test_rrf_rewards_documents_present_in_both_lists() -> None:
    fused = rrf_fuse([[1, 2, 3], [3, 1, 4]], k=60)
    assert [item_id for item_id, _ in fused[:2]] == [1, 3]


def test_rrf_is_deterministic() -> None:
    rankings = [[4, 2], [2, 4]]
    assert rrf_fuse(rankings) == rrf_fuse(rankings)
