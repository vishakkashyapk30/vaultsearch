"""Ingest raw source files into normalized documents and chunks.

Reads data/sources/*.json, validates against the shared schema, chunks
document bodies, and writes data/chunks.json used by the indexers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.schema import Chunk, Document

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Roughly 160 tokens per chunk for ordinary English, with enough overlap to
# preserve facts that cross a boundary.
CHUNK_WORDS = 120
OVERLAP_WORDS = 20


def chunk_document(doc: Document) -> list[Chunk]:
    """Split a document body into overlapping word-window chunks.

    Every chunk inherits the parent document's ACL verbatim — chunking
    must never widen access.
    """
    words = doc.body.split()
    chunks: list[Chunk] = []
    start = 0
    idx = 0
    while start < len(words):
        window = words[start : start + CHUNK_WORDS]
        text = " ".join(window)
        chunks.append(
            Chunk(
                chunk_id=f"{doc.doc_id}#c{idx}",
                doc_id=doc.doc_id,
                source=doc.source,
                title=doc.title,
                text=text,
                allowed_principals=list(doc.allowed_principals),
            )
        )
        idx += 1
        if start + CHUNK_WORDS >= len(words):
            break
        start += CHUNK_WORDS - OVERLAP_WORDS
    return chunks


def load_documents() -> list[Document]:
    docs: list[Document] = []
    for path in sorted((DATA_DIR / "sources").glob("*.json")):
        for raw in json.loads(path.read_text()):
            docs.append(Document.from_dict(raw))
    return docs


def main() -> None:
    docs = load_documents()
    if not docs:
        raise SystemExit("No source data found. Run ingestion/generate_data.py first.")
    all_chunks: list[Chunk] = []
    for doc in docs:
        all_chunks.extend(chunk_document(doc))
    out = DATA_DIR / "chunks.json"
    out.write_text(json.dumps([c.to_dict() for c in all_chunks], indent=2))
    by_source: dict[str, int] = {}
    for d in docs:
        by_source[d.source] = by_source.get(d.source, 0) + 1
    print(f"documents: {len(docs)} {by_source}")
    print(f"chunks:    {len(all_chunks)} -> {out}")


if __name__ == "__main__":
    main()
