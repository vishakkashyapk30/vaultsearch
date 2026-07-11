from app.acl import IdentityStore
from app.agents import PermissionVerifier
from app.retrieval_core import RetrievedChunk


def chunk(chunk_id: str, acl: list[str]) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        doc_id=chunk_id,
        source="drive",
        title="Test",
        text="evidence",
        allowed_principals=acl,
        score=1.0,
    )


def test_verifier_rejects_chunk_not_allowed_for_user() -> None:
    identity = IdentityStore({"user:eng": ["group:engineering"]})
    verifier = PermissionVerifier(identity)
    verified, rejected = verifier.verify(
        "user:eng",
        [
            chunk("allowed", ["group:engineering"]),
            chunk("restricted", ["group:finance"]),
        ],
    )
    assert [item.chunk_id for item in verified] == ["allowed"]
    assert rejected == ["restricted"]
