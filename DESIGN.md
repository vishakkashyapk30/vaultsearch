# VaultSearch Design

## Goals

VaultSearch demonstrates secure enterprise retrieval on a single machine:
ingest heterogeneous sources into one schema, enforce source permissions,
measure hybrid retrieval quality, and produce grounded answers through a
locally hosted LLM. The primary invariant is stronger than answer-level
redaction: unauthorized text must never reach ranking, reranking, or the LLM.

Non-goals include production-scale tenancy, real third-party connectors,
distributed indexing, and high availability.

## Architecture

The ingestion layer normalizes Slack-like threads, Drive-like documents, and
ticket records into `Document`, then creates overlapping `Chunk` records.
Each chunk inherits its parent ACL exactly.

The indexing layer builds:

- a BM25 index over tokenized title and body text;
- a FAISS inner-product index over normalized MiniLM embeddings;
- a metadata array that maps index positions to chunk IDs, source data, and
  ACLs.

For `(user_id, query)`, retrieval follows this sequence:

1. Expand the user into `{user_id, group_ids...}`.
2. Compute the set of chunk IDs whose ACL intersects those principals.
3. Score only those IDs with BM25 `get_batch_scores`.
4. Restrict FAISS search with `IDSelectorArray`.
5. Fuse result ranks with Reciprocal Rank Fusion.
6. Rerank the authorized fused candidates with a cross-encoder.
7. Independently recheck every result before synthesis.

The orchestrator asks Ollama for a bounded list of standalone subqueries,
executes each through the same secured retriever, deduplicates results, verifies
permissions in deterministic code, and asks Ollama to synthesize an answer
using only verified evidence. Citations are intersected with verified document
IDs before being returned.

## Security invariants

- Unknown users have no principals and retrieve nothing.
- Empty ACLs deny access except to an explicit admin group.
- Chunking cannot widen access.
- ACLs are applied before both sparse and dense scoring.
- LLM output never grants access and cannot create a valid citation to an
  unauthorized document.
- A second verifier detects regressions in the retrieval boundary.
- Audit logs record user, plan, candidate counts, citations, verification
  rejections, and stage timings without logging hidden restricted candidates.

Tests exercise each invariant. The adversarial evaluation additionally searches
returned text for distinctive secrets from documents the test user cannot read.

## Key trade-offs

### Pre-filter versus post-filter

Post-filtering a global top-k can leak data into rerankers or prompts and can
return too few permitted results. Pre-filtering avoids both problems. The local
implementation calculates authorized IDs per query; a production service would
cache user/group expansion and maintain compressed ACL bitmaps.

### RRF versus learned fusion

Sparse and dense scores have incompatible scales. RRF is deterministic,
training-free, and robust enough for this corpus. Learned fusion may improve
quality but adds labels, drift, and debugging complexity.

### Local FAISS versus managed vector storage

FAISS is zero-cost and makes the permission boundary visible in code. It lacks
distributed durability, incremental replication, and native metadata indexing.
At enterprise scale, the same interface would sit over a sharded vector service
with server-side ACL predicates.

### Local Ollama versus a hosted model

Ollama removes cloud cost and keeps evidence on the machine. It has lower
throughput and weaker structured-output reliability than some hosted models, so
planning has strict validation and a safe single-query fallback. Authorization
does not depend on model reliability.

## Reliability and observability

Models and indexes load once during the FastAPI lifespan. `/health` supports
container health checks. Every `/ask` emits one JSON-line audit event. Retrieval
reports per-stage latency, candidate counts, and total latency. If Ollama is
unavailable, retrieval remains functional and synthesis returns a bounded
availability message rather than fabricated content.

## Scaling path

A production version would add incremental connector checkpoints, document
versioning and deletion, sharded sparse/vector indexes, cached ACL expansion,
group-membership invalidation, replicas, backpressure, request deadlines,
distributed tracing, and SLO dashboards. Security tests would include nested
groups, revocation races, malformed connector ACLs, and tenant isolation.
