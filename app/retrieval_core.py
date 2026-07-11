"""Permission-aware hybrid retrieval with ACL pre-filtering."""

from __future__ import annotations

import json
import pickle
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import faiss
import numpy as np

from .acl import IdentityStore, can_access

EMBED_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
RERANK_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def rrf_fuse(rankings: list[list[int]], k: int = 60) -> list[tuple[int, float]]:
    """Apply Reciprocal Rank Fusion to ranked chunk indices."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda item: -item[1])


@dataclass
class RetrievedChunk:
    chunk_id: str
    doc_id: str
    source: str
    title: str
    text: str
    allowed_principals: list[str]
    score: float


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    stage_latency_ms: dict[str, float] = field(default_factory=dict)
    candidates_allowed: int = 0
    candidates_total: int = 0


class Retriever:
    def __init__(
        self,
        index_dir: str | Path,
        identity: IdentityStore,
        use_reranker: bool = True,
    ):
        index_dir = Path(index_dir)
        self.identity = identity
        with open(index_dir / "bm25.pkl", "rb") as file:
            self.bm25 = pickle.load(file)
        self.faiss_index = faiss.read_index(str(index_dir / "vectors.faiss"))
        self.chunks: list[dict] = json.loads(
            (index_dir / "chunks_meta.json").read_text()
        )

        from sentence_transformers import SentenceTransformer

        self.embedder = SentenceTransformer(EMBED_MODEL_NAME)
        self.reranker = None
        if use_reranker:
            from sentence_transformers import CrossEncoder

            self.reranker = CrossEncoder(RERANK_MODEL_NAME)

    def allowed_chunk_ids(self, principals: set[str]) -> list[int]:
        return [
            index
            for index, chunk in enumerate(self.chunks)
            if can_access(principals, chunk["allowed_principals"])
        ]

    def _bm25_search(
        self, query: str, allowed: list[int], top_k: int
    ) -> list[int]:
        scores = self.bm25.get_batch_scores(tokenize(query), allowed)
        order = np.argsort(scores)[::-1][:top_k]
        return [allowed[index] for index in order if scores[index] > 0]

    def _vector_search(
        self, query: str, allowed: list[int], top_k: int
    ) -> list[int]:
        query_vector = self.embedder.encode(
            [query], normalize_embeddings=True
        ).astype(np.float32)
        selector = faiss.IDSelectorArray(np.asarray(allowed, dtype=np.int64))
        params = faiss.SearchParameters(sel=selector)
        _, ids = self.faiss_index.search(
            query_vector,
            min(top_k, len(allowed)),
            params=params,
        )
        return [int(index) for index in ids[0] if index >= 0]

    def _rerank(
        self, query: str, candidate_ids: list[int], top_n: int
    ) -> list[tuple[int, float]]:
        pairs = [
            (
                query,
                f"{self.chunks[index]['title']} {self.chunks[index]['text']}",
            )
            for index in candidate_ids
        ]
        scores = self.reranker.predict(pairs)
        order = np.argsort(scores)[::-1][:top_n]
        return [(candidate_ids[index], float(scores[index])) for index in order]

    def search(
        self,
        user_id: str,
        query: str,
        top_k: int = 30,
        top_n: int = 8,
        mode: str = "hybrid+rerank",
    ) -> RetrievalResult:
        latency: dict[str, float] = {}
        total_start = time.perf_counter()
        principals = self.identity.expand_principals(user_id)
        allowed = self.allowed_chunk_ids(principals)
        latency["acl_filter"] = (time.perf_counter() - total_start) * 1000

        result = RetrievalResult(
            chunks=[],
            stage_latency_ms=latency,
            candidates_allowed=len(allowed),
            candidates_total=len(self.chunks),
        )
        if not allowed:
            return result

        bm25_ids: list[int] = []
        vector_ids: list[int] = []
        if mode in ("bm25", "hybrid", "hybrid+rerank"):
            start = time.perf_counter()
            bm25_ids = self._bm25_search(query, allowed, top_k)
            latency["bm25"] = (time.perf_counter() - start) * 1000
        if mode in ("vector", "hybrid", "hybrid+rerank"):
            start = time.perf_counter()
            vector_ids = self._vector_search(query, allowed, top_k)
            latency["vector"] = (time.perf_counter() - start) * 1000

        if mode == "bm25":
            ranked = [
                (index, 1.0 / (rank + 1))
                for rank, index in enumerate(bm25_ids)
            ][:top_n]
        elif mode == "vector":
            ranked = [
                (index, 1.0 / (rank + 1))
                for rank, index in enumerate(vector_ids)
            ][:top_n]
        else:
            start = time.perf_counter()
            fused = rrf_fuse([bm25_ids, vector_ids])
            latency["rrf"] = (time.perf_counter() - start) * 1000
            if mode == "hybrid+rerank" and self.reranker is not None and fused:
                start = time.perf_counter()
                ranked = self._rerank(
                    query,
                    [index for index, _ in fused[: max(top_n * 3, 20)]],
                    top_n,
                )
                latency["rerank"] = (time.perf_counter() - start) * 1000
            else:
                ranked = fused[:top_n]

        for index, score in ranked:
            chunk = self.chunks[index]
            result.chunks.append(
                RetrievedChunk(
                    chunk_id=chunk["chunk_id"],
                    doc_id=chunk["doc_id"],
                    source=chunk["source"],
                    title=chunk["title"],
                    text=chunk["text"],
                    allowed_principals=chunk["allowed_principals"],
                    score=score,
                )
            )
        latency["total"] = (time.perf_counter() - total_start) * 1000
        return result
