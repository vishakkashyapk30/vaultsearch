from app.schema import Document
from ingestion.ingest import CHUNK_WORDS, OVERLAP_WORDS, chunk_document


def document(body: str, acl: list[str] | None = None) -> Document:
    return Document(
        doc_id="doc-1",
        source="drive",
        title="Test",
        body=body,
        allowed_principals=acl if acl is not None else ["group:engineering"],
        created_at="2026-01-01T00:00:00",
    )


def test_short_document_produces_one_chunk_with_inherited_acl() -> None:
    chunks = chunk_document(document("a short document"))
    assert len(chunks) == 1
    assert chunks[0].allowed_principals == ["group:engineering"]
    assert chunks[0].doc_id == "doc-1"


def test_long_document_overlaps_chunk_boundaries() -> None:
    words = [f"word{i}" for i in range(CHUNK_WORDS + 20)]
    chunks = chunk_document(document(" ".join(words)))
    assert len(chunks) == 2
    first = chunks[0].text.split()
    second = chunks[1].text.split()
    assert first[-OVERLAP_WORDS:] == second[:OVERLAP_WORDS]


def test_empty_acl_remains_empty() -> None:
    assert chunk_document(document("restricted", acl=[]))[0].allowed_principals == []
