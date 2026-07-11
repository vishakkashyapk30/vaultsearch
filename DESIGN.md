# VaultSearch Design

## Goals

VaultSearch is a reference implementation of a **secure retrieval boundary for
RAG**: given a user and a question, produce a grounded, cited answer while
guaranteeing that no content the user is not authorized to read can influence
the answer, appear in a citation, or be inferred from the response. Enterprise
search over heterogeneous sources is the demo scenario, but the boundary itself
is general — it applies to any multi-tenant or permissioned RAG application.

The primary invariant is stronger than answer-level redaction: unauthorized
text must never reach ranking, reranking, or the model's context in the first
place, and everything the model produces is treated as untrusted output.

Non-goals include production-scale tenancy, real third-party connectors,
distributed indexing, and high availability. Where those matter, the scaling
path below and the scale study in `reports/` describe what would change.

## Threat model

The adversary is a legitimate, authenticated user trying to obtain content
outside their permissions, by any of:

- crafting queries designed to surface restricted documents;
- planting prompt-injection instructions in documents they *can* write/read,
  hoping the model will reveal other teams' data;
- relying on the model to fabricate or forge citations to restricted material;
- inferring, from answers or refusals, that a restricted document exists.

Out of scope: a compromised host, a malicious operator, side channels below the
application, and incorrect ACLs supplied by an upstream source of truth
(VaultSearch enforces the ACLs it is given; it does not adjudicate them).

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

The FastAPI layer serves both the JSON API and a static web interface. `/api/ask`
returns the answer, citations, the verified evidence, a stage-by-stage trace,
and per-stage latency. `/api/search` runs all four retrieval modes over the same
authorized candidate set for side-by-side comparison. `/api/users` reports each
identity and how many chunks it can see. The UI is dependency-free HTML/CSS/JS
so it needs no build step and is easy to audit.

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

## Attacking the model layer

Retrieval-time filtering only protects the boundary if the layer above it can't
undo it. The red-team study (`redteam/run_redteam.py`, report in `reports/`)
attacks the model directly and reports observed behavior:

- **Prompt injection.** Documents readable by everyone are seeded with
  instructions telling the model to ignore permissions and reveal other teams'
  secrets. Because restricted documents are never retrieved, the secrets are not
  in context, so the injection cannot exfiltrate them — the structural guarantee
  holds regardless of model compliance.
- **Citation forgery.** The local model does invent citations (observed in ~40%
  of raw responses in one run — e.g., ticket display keys or made-up IDs). This
  is a genuine model-quality failure, which is why the server strips every
  citation that does not map to verified evidence; none survive to the user.
- **Existence inference.** When no authorized evidence answers a question, the
  refusal is produced by deterministic code with no LLM call, so a
  restricted-but-hidden topic is indistinguishable from a nonexistent one.

The lesson encoded in the design: keep authorization in deterministic code
below the model, and treat model output (text and citations) as untrusted.

## Key trade-offs

### Pre-filter versus post-filter

Post-filtering a global top-k can leak data into rerankers or prompts and can
return too few permitted results. Pre-filtering avoids both problems. The scale
study (`reports/scale_study.md`) quantifies this up to one million vectors: with
a fixed over-fetch budget, post-filter recall collapses to ~0.45 once a user can
see only 0.2% of the corpus, while pre-filter recall stays exact and its latency
*drops* as ACLs tighten (the selector confines the scan to authorized IDs). The
local implementation calculates authorized IDs per query; a production service
would cache user/group expansion and maintain compressed ACL bitmaps, and move
from a flat index to a sharded ANN index with ACL-aware partitioning.

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
