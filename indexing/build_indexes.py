"""Build the BM25 and FAISS indexes over data/chunks.json.

Artifacts written to indexes/:
  bm25.pkl        - tokenized corpus + BM25 state
  vectors.faiss   - FAISS inner-product index over normalized embeddings
  chunks_meta.json- chunk metadata (id, doc_id, ACL, text) in index order
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import faiss
import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from app.retrieval_core import EMBED_MODEL_NAME, tokenize

ROOT = Path(__file__).resolve().parent.parent
INDEX_DIR = ROOT / "indexes"


def main() -> None:
    chunks = json.loads((ROOT / "data" / "chunks.json").read_text())
    if not chunks:
        raise SystemExit("No chunks found. Run ingestion/ingest.py first.")
    INDEX_DIR.mkdir(exist_ok=True)

    texts = [f"{c['title']} {c['text']}" for c in chunks]

    print(f"Building BM25 over {len(chunks)} chunks...")
    tokenized = [tokenize(t) for t in texts]
    bm25 = BM25Okapi(tokenized)
    with open(INDEX_DIR / "bm25.pkl", "wb") as f:
        pickle.dump(bm25, f)

    print(f"Embedding with {EMBED_MODEL_NAME}...")
    model = SentenceTransformer(EMBED_MODEL_NAME)
    emb = model.encode(texts, batch_size=64, show_progress_bar=True, normalize_embeddings=True)
    emb = np.asarray(emb, dtype=np.float32)

    index = faiss.IndexFlatIP(emb.shape[1])
    index.add(emb)
    faiss.write_index(index, str(INDEX_DIR / "vectors.faiss"))

    (INDEX_DIR / "chunks_meta.json").write_text(json.dumps(chunks))
    print(f"Done. {len(chunks)} chunks indexed -> {INDEX_DIR}")


if __name__ == "__main__":
    main()
