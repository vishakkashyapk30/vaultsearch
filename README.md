# VaultSearch

Ask questions over a company's documents and get a cited answer, where every
user only ever sees what their permissions allow — enforced *before* search
runs, not patched on afterward.

---

## Quick start

Pick the path that suits you. Both end at the same place: a running app at
**[http://localhost:8000](http://localhost:8000)**.

### Option A — Docker (recommended, no Python required)

You need: **[Docker Desktop](https://www.docker.com/products/docker-desktop/)** and **[Git](https://git-scm.com/downloads)**.

```bash
git clone https://github.com/vishakkashyapk30/vaultsearch.git
cd vaultsearch
docker compose up --build
```

That single command:
1. Pulls the `ollama/ollama` image and downloads the `gemma3:4b` language model (~3 GB — takes a few minutes the first time, cached on subsequent runs).
2. Builds the VaultSearch image, generates the synthetic company corpus (225 documents), and creates the search indexes.
3. Starts the API and web UI on port 8000.

Open **[http://localhost:8000](http://localhost:8000)** once you see `Application startup complete` in the logs.

> **Why is the first start slow?** Three things happen only once: the model download (~3 GB), the PyTorch/HuggingFace model download for the embedding and reranking models (~300 MB), and building the search indexes. All of these are cached in Docker volumes — subsequent `docker compose up` takes about 10 seconds.

To stop everything: `Ctrl+C` then `docker compose down`.

---

### Option B — Plain Python (no Docker)

You need: **Python 3.11+**, **[Ollama](https://ollama.com/download)**, and **Git**.

```bash
# 1. Download the project
git clone https://github.com/vishakkashyapk30/vaultsearch.git
cd vaultsearch

# 2. Pull the language model (≈ 3 GB, one-time download)
ollama pull gemma3:4b

# 3. Create a virtual environment and install dependencies
./setup.sh && source .venv/bin/activate
# Windows: python -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt

# 4. Generate the synthetic company and build search indexes
python ingestion/generate_data.py
python ingestion/ingest.py
python indexing/build_indexes.py

# 5. Start the server
uvicorn app.api:app --port 8000
```

Open **[http://localhost:8000](http://localhost:8000)**.

> **Using Cursor or another Linux AppImage editor?** The editor's `APPIMAGE`
> environment variable can confuse Python's venv creation. `./setup.sh` already
> handles this. If you're creating the venv manually, run:
> `rm -rf .venv && env -u APPIMAGE python3 -m venv .venv`

---

## Using the app

**Ask tab.** Choose a person in the left sidebar — each card shows how much of
the 513-chunk corpus that identity can see. Type a question and you'll get a
cited answer, the exact evidence chunks the model was allowed to read, and a
step-by-step trace showing how many documents were excluded by permissions
*before* any searching happened.

**Compare tab.** Enter a query and see keyword (BM25), semantic (vector),
hybrid (RRF fusion), and reranked results side by side, all over the same
permission-filtered candidate set.

**Things worth trying:**

- Ask *"What is the Q3 infrastructure budget?"* as **Dmitri (finance)** and then
  as **Asha (engineering)**. Same question, different evidence, different answer.
  The trace shows the finance chunks were never even ranked for Asha.
- Ask *"What is the Project Hawk offer range?"* as **Elena (leadership)** vs
  **Ines (all-staff only)**. As Ines the refusal looks identical to asking
  about something that doesn't exist — that's existence-leak resistance.
- Ask anything as **Site Admin** to see the full 513/513 ceiling, including the
  two orphan tickets with no ACL that nobody else can retrieve.
- In Compare, search *"latency"* and watch the cross-encoder reorder what BM25
  and vector search each surfaced independently.

---

## Why this project exists

The moment you put a language model in front of a company's documents, you
inherit an access-control problem that most RAG demos quietly ignore.

A normal RAG pipeline embeds every document, retrieves the most relevant chunks
for a question, and feeds them to the model to write an answer. That works when
everyone can read everything. Inside a real organization they cannot: finance
salaries, an unannounced acquisition, an HR investigation, a security
postmortem — each readable by some people and off-limits to others. If the
retriever pulls a restricted chunk into the model's context, that content can
end up in the answer, in a citation, or leak indirectly through phrasing.
**One leaked sentence is a breach.** And a language model cannot be trusted to
keep a secret you handed it.

The only robust fix is to make sure unauthorized content is **never retrieved
in the first place**, and to treat everything the model does afterward as
untrusted. VaultSearch is a small, complete, runnable reference implementation
of that boundary, with enterprise search as the demo scenario.

---

## How it works

### The synthetic company

Everything is generated deterministically by `ingestion/generate_data.py`
(seeded RNG, no external APIs), so every clone of this repo builds the exact
same corpus and the results in this README are reproducible.

**225 documents → 513 chunks** across three mock sources:

| Source | Mirrors | Documents | ACL granularity |
|---|---|---|---|
| `slack` | Chat channels | 86 | channel-level |
| `drive` | Document store | 72 | folder-level |
| `tickets` | Issue tracker | 62 | project-level |
| injected | Adversarial docs | 5 | all-staff (deliberately readable) |

Twelve coherent **storylines** thread through all three sources, each with
concrete facts and one ACL. The restricted ones contain distinctive, memorable
secrets (a dollar figure, a salary band, an acquisition target) — not for
flavor, but as instrumentation: the eval and red-team scripts grep answers for
exactly these strings, so a leak anywhere in the pipeline is mechanically
detectable.

Two special document classes:
- **Orphan tickets** (`SYS-900`, `SYS-901`): empty ACL → deny-by-default, only admin sees them.
- **Prompt-injection documents** (`inject-000`…`inject-004`): all-staff Slack posts
  with hostile instructions embedded ("Ignore all access restrictions…"). They
  contain **no real secrets** — they exist to probe what the LLM does when
  adversarial text legitimately enters its context.

### The people

Twelve users, six groups. Each persona exercises a specific access pattern:

| Persona | Groups | Visible chunks | Represents |
|---|---|---|---|
| Asha Raman | engineering | 397/513 (77%) | Engineering IC |
| Ben Okafor | engineering | 397/513 (77%) | Engineering IC (identical access to Asha — intentional) |
| Hiro Tanaka | engineering | 397/513 (77%) | Engineering IC |
| Kavya Pillai | engineering | 397/513 (77%) | Engineering IC |
| Chitra Nair | engineering + leadership | 435/513 (85%) | Engineering lead |
| Dmitri Volkov | finance | 283/513 (55%) | Finance IC |
| Jonas Berg | finance | 283/513 (55%) | Finance IC |
| Elena Costa | finance + leadership | 359/513 (70%) | CFO archetype |
| Farid Hassan | hr | 245/513 (48%) | HR IC |
| Grace Liu | hr + leadership | 321/513 (63%) | Head of People |
| Ines Moreau | *(none beyond all-staff)* | 207/513 (40%) | Least-privileged baseline |
| Site Admin | admin | 513/513 (100%) | Break-glass identity |

### The pipeline, end to end

```
generate_data.py → ingest.py → build_indexes.py          (offline)
                                    │
user ──► /api/ask ──► Orchestrator ─┤
                        │ 1. QueryPlanner (LLM)          app/agents.py
                        │ 2. Tool loop (bounded):        app/tools.py
                        │      search / lookup_person / list_my_sources
                        │      each ACL-enforced          app/retrieval_core.py
                        │      ACL pre-filter → BM25 ∥ FAISS → RRF → rerank
                        │ 3. EvidenceAssessor (LLM): sufficient? refine & repeat
                        │ 4. PermissionVerifier (deterministic re-check)
                        │ 5. AnswerSynthesizer (LLM, grounded prompt)
                        │ 6. Citation sanitizer (deterministic)
                        │ 7. GroundednessCritic (LLM, advisory only)
                        └──► answer + citations + evidence + trace + audit log
```

**Stage 1 — Ingestion and chunking** (`ingestion/ingest.py`). Every source is
normalized to one schema and split into 120-word overlapping chunks. The
critical invariant: **every chunk inherits its parent document's ACL verbatim**.
Chunking can never widen access.

**Stage 2 — Indexing** (`indexing/build_indexes.py`). Three artifacts:
- `bm25.pkl` — `BM25Okapi` over tokenized title + body.
- `vectors.faiss` — `IndexFlatIP` of 384-dim MiniLM embeddings (L2-normalized
  so inner product = cosine similarity). Exact/brute-force is deliberate: zero
  recall loss, and flat scan is still single-digit milliseconds at this scale.
- `chunks_meta.json` — chunk metadata in index order, so the ACL rides alongside
  the vector and the retriever never joins a second store to make a security decision.

**Stage 3 — Identity and the ACL primitive** (`app/acl.py`). Two operations:

```python
expand_principals("user:dmitri")
# → {"user:dmitri", "group:all-staff", "group:finance"}

def can_access(principals, allowed_principals):
    if ADMIN_GROUP in principals: return True      # admin bypass
    if not allowed_principals:    return False     # empty ACL → deny
    return bool(principals.intersection(allowed_principals))
```

Unknown users expand to the empty set and see nothing. Empty ACLs deny everyone
except admins. This is six lines of pure deterministic code — exhaustively unit-testable, never a judgment call.

**Stage 4 — Permission-aware hybrid retrieval** (`app/retrieval_core.py`).

1. Expand the user's principals.
2. Pre-filter: scan chunk metadata, collect indices of every chunk the user may read.
3. BM25 over the allowed subset only (`get_batch_scores(query_tokens, allowed_ids)`).
4. Vector search with an `IDSelectorArray` — unauthorized vectors excluded *inside* the FAISS scan.
5. Reciprocal Rank Fusion: $\text{RRF}(c) = \sum_{r} \frac{1}{k + \text{rank}_r(c) + 1}$ ($k=60$). Rank-based, so BM25 and cosine scores never need to be on the same scale.
6. Cross-encoder reranking of the fused top candidates.

**Stage 5 — The agent layer** (`app/agents.py`, `app/tools.py`). An iterative
tool-using loop, with LLM calls at four points and deterministic code at every
enforcement boundary:

- **`QueryPlanner`** decomposes the question into up to 4 retrieval queries (JSON, with fallback to raw question if the LLM fails).
- **`Toolbox`** (`app/tools.py`) is bound to one user identity at construction. No tool accepts a `user_id` argument — the model chooses *what* to call, never *whose* permissions apply. Three tools: `search(query)`, `lookup_person(name)`, `list_my_sources()`. `validate_tool_call` rejects unknown tools, malformed arguments, and silently drops smuggled fields.
- **`EvidenceAssessor`** reviews the current evidence after each round and returns a JSON verdict: `{"sufficient": true/false, "tool_calls": [...]}`. Additional calls are validated and executed, bounded by `AGENT_MAX_ROUNDS`. LLM failure ends the loop gracefully.
- **`PermissionVerifier`** re-runs `can_access` over the final merged chunk set — after the loop, so no amount of agentic tool use can route around it. Defense in depth: in a correct system this rejects nothing, but it contains any future retrieval-layer regression before anything reaches a prompt.
- **`AnswerSynthesizer`** builds a prompt containing only verified evidence and asks the model to cite every factual claim.
- **Citation sanitizer** strips any `[doc-id]` the model emitted that isn't a verified evidence document. This is why the red-team study found 0 forged citations reaching users, even though the raw model output contained forged citations in ~40% of responses.
- **`GroundednessCritic`** is a final advisory LLM pass: `grounded / partially_grounded / ungrounded`. Shown in the trace, enforces nothing.

**Stage 6 — API, audit, and the web app** (`app/api.py`). FastAPI service:

| Endpoint | Purpose |
|---|---|
| `POST /api/ask` | Full agentic pipeline → `{answer, citations, evidence, trace, latency_ms}` |
| `POST /api/search` | All four retrieval modes side by side (powers the Compare tab) |
| `GET /api/users` | Persona directory with live per-user visible-chunk counts |
| `GET /health` | Liveness |

Every `/api/ask` appends a structured JSON line to `logs/audit.jsonl`. If
`AUDIT_DYNAMODB_TABLE` is set, it also writes to DynamoDB — queryable by
`user_id` in one `Query` call.

---

## The trust boundary

The design thesis: **the LLM can make quality decisions, never access decisions**.

| Decision | Made by | Why |
|---|---|---|
| What to search for | LLM (planner) | Wrong = worse answer, not a breach |
| Whether to search again | LLM (assessor) | Wrong = one wasted round, not a breach |
| Which tools exist | `validate_tool_call` (pure code) | Unknown tools rejected |
| *Whose* permissions apply | `Toolbox` binding (pure code) | Identity is out-of-band |
| What the user may read | `can_access` (pure code) | Must be provable and testable |
| What enters the prompt | Pre-filter + verifier (pure code) | Model can't leak what it never received |
| How to phrase the answer | LLM (synthesizer) | Grounded in verified evidence only |
| Which citations survive | Sanitizer (pure code) | Model output is untrusted input |
| Is the answer grounded | LLM (critic, advisory) | A judgment, never enforced |

Making the agent loop *more* capable (more rounds, more tools, smarter
refinement) increases its authority over retrieval quality while adding zero
authority over access. The agent-loop tests assert this directly — an assessor
that aggressively retries restricted topics never widens what the user sees.

---

## Results

All numbers are reproducible with the scripts in `eval/` and `redteam/`.

### Retrieval quality and latency

| Mode | NDCG@10 | MRR | p50 latency | p95 latency |
|---|---|---|---|---|
| Keyword (BM25) | 0.783 | 0.708 | 1.4 ms | 4.4 ms |
| Semantic (vector) | 0.853 | 0.792 | 5.1 ms | 403 ms |
| Hybrid (RRF) | 0.843 | 0.750 | 7.3 ms | 19.5 ms |
| **Hybrid + reranker** | **0.865** | **0.792** | 50 ms | 81 ms |

Permission safety: **0 leaks across 100 adversarial retrieval attempts**.

### Red-team of the LLM layer

| Attack | Real breaches | Finding |
|---|---|---|
| Prompt-injection exfiltration | 0 | Injection can't exfiltrate secrets that were never retrieved |
| Citation forgery | 0 | Model forged citations in raw output; all stripped by sanitizer |
| Existence inference | 0 | Restricted-but-hidden topic = nonexistent topic from the user's view |

### Scaling study (pre-filter vs. post-filter, up to 1M vectors)

Post-filtering with a fixed over-fetch budget silently loses correct results
once a user can see only a small slice of the corpus (recall drops to ~0.45 at
0.2% visibility). Pre-filtering stays exact *and* gets faster as ACLs tighten —
the selector confines the scan to authorized indices only.

---

## Running the evaluations yourself

```bash
# Unit + integration tests (run without Ollama or heavy models)
python -m pytest -q

# Retrieval quality, latency percentiles, and 100 adversarial permission-leak probes
python eval/evaluate.py

# LLM-layer red-team: prompt injection, citation forgery, existence inference
# (Ollama must be running)
python redteam/run_redteam.py

# Pre/post-filter scaling study up to 1M vectors (no models needed)
python eval/scale_study.py
```

---

## Using VaultSearch from other agents (MCP)

`mcp_server.py` exposes the same permission boundary as
[Model Context Protocol](https://modelcontextprotocol.io) tools, so any
MCP-capable agent (Claude Desktop, Cursor, a LangGraph app) can use
VaultSearch as a safe retrieval tool. The identity is pinned per server
process — `VAULTSEARCH_USER=user:asha python mcp_server.py` — and is not a
tool parameter, so the calling model cannot query as anyone else.

Four tools: `ask` (full cited answer), `search` (raw permitted evidence),
`lookup_person` (directory lookup), `whoami` (the bound identity + visibility).

The VaultSearch API must be running. Point the MCP server at it with
`VAULTSEARCH_URL` (default `http://127.0.0.1:8000`).

Example configuration for Cursor or Claude Desktop:

```json
{
  "mcpServers": {
    "vaultsearch": {
      "command": "/path/to/vaultsearch/.venv/bin/python",
      "args": ["/path/to/vaultsearch/mcp_server.py"],
      "env": { "VAULTSEARCH_USER": "user:asha" }
    }
  }
}
```

---

## The cloud-native layer (LocalStack + Terraform)

The core demo is entirely cloud-free. The `cloud/` directory adds an **optional**
layer that restructures the offline pipeline around real AWS primitives —
provisioned on your machine with [LocalStack](https://www.localstack.cloud), so
it still costs nothing and needs no AWS account. The same Terraform applies
unchanged to real AWS.

What it provisions (`cloud/main.tf`):

| Resource | Replaces | Role |
|---|---|---|
| S3 `vaultsearch-sources` | `data/sources/` on disk | Raw connector documents |
| S3 event → SQS `vaultsearch-ingest` | Running `ingest.py` by hand | Event-driven re-indexing |
| S3 `vaultsearch-artifacts` | `indexes/` on disk | Built index distribution |
| DynamoDB `vaultsearch-audit` | `logs/audit.jsonl` (mirrored, not replaced) | Queryable audit trail |

### Running it

You need a free LocalStack auth token from [app.localstack.cloud](https://app.localstack.cloud)
(2026+ images require one even on the free tier) and
[Terraform](https://developer.hashicorp.com/terraform/install).

```bash
# 1. Add your token to .env
cp .env.example .env
# Edit .env and set: LOCALSTACK_AUTH_TOKEN=your-token-here

# 2. Start LocalStack and provision the AWS resources
docker compose --profile cloud up -d localstack
cd cloud && terraform init && terraform apply

# 3. Upload the source documents (fires S3 events into the SQS queue)
cd ..
python cloud/sync_sources.py

# 4. Run the event-driven ingest worker (consume SQS → rebuild → publish indexes)
python cloud/ingest_worker.py --once

# 5. Start the API with the DynamoDB audit mirror
AUDIT_DYNAMODB_TABLE=vaultsearch-audit \
AWS_ENDPOINT_URL=http://localhost:4566 \
uvicorn app.api:app --port 8000
```

Query the audit trail like a security reviewer would:

```bash
AWS_ENDPOINT_URL=http://localhost:4566 \
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-1 \
aws dynamodb query \
  --table-name vaultsearch-audit \
  --key-condition-expression "user_id = :u" \
  --expression-attribute-values '{":u":{"S":"user:dmitri"}}'
```

To deploy to real AWS: run `terraform apply` without the `localstack_endpoint`
variable and supply real credentials. No Python code changes — boto3 reads
`AWS_ENDPOINT_URL` from the environment.

---

## Configuration

Set via environment variables or in `.env` (see `.env.example`).

| Variable | Default | What it does |
|---|---|---|
| `OLLAMA_MODEL` | `gemma3:4b` | Which local model to use |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama endpoint (Docker Compose sets this to `http://ollama:11434` automatically) |
| `USE_RERANKER` | `true` | Set to `false` to skip the cross-encoder for lower latency |
| `AGENT_MAX_ROUNDS` | `2` | Max evidence-refinement rounds after the initial plan (`0` disables iteration) |
| `USE_CRITIC` | `true` | Set to `false` to skip the advisory groundedness critic |
| `AUDIT_DYNAMODB_TABLE` | *(empty)* | Mirror audit events to this DynamoDB table (cloud layer) |

---

## Project layout

```
vaultsearch/
├── app/
│   ├── schema.py                   Document / Chunk dataclasses
│   ├── acl.py                      IdentityStore + can_access() — the security primitive
│   ├── retrieval_core.py           ACL pre-filter → BM25 ∥ FAISS → RRF → rerank
│   ├── tools.py                    Identity-bound, permission-gated tools + validation
│   ├── agents.py                   Orchestrator, QueryPlanner, EvidenceAssessor,
│   │                               PermissionVerifier, AnswerSynthesizer,
│   │                               GroundednessCritic, citation sanitizer
│   ├── ollama_client.py            Minimal HTTP client for Ollama (chat + JSON mode)
│   ├── cloud_audit.py              Optional best-effort DynamoDB audit mirror
│   └── api.py                      FastAPI: /api/ask, /api/search, /api/users, /health
├── mcp_server.py                   MCP server exposing VaultSearch as safe agent tools
├── cloud/
│   ├── main.tf                     Terraform: S3, SQS eventing, DynamoDB audit
│   ├── sync_sources.py             Upload raw sources to S3 (fires ingest events)
│   ├── ingest_worker.py            SQS consumer → re-ingest → publish indexes
│   └── requirements.txt            boto3
├── web/
│   ├── index.html                  Layout: persona sidebar, Ask / Compare / About tabs
│   ├── style.css                   Light theme, Poppins, CSS design tokens
│   └── app.js                      State, API calls, answer/evidence/trace rendering
├── ingestion/
│   ├── generate_data.py            Deterministic synthetic corpus (12 storylines, 3 sources)
│   └── ingest.py                   Normalization + 120-word/20-overlap chunking
├── indexing/
│   └── build_indexes.py            Builds bm25.pkl, vectors.faiss, chunks_meta.json
├── eval/
│   ├── evaluate.py                 NDCG@10 / MRR / latency + 100 permission-leak probes
│   └── scale_study.py             Pre/post-filter scaling up to 1M vectors
├── redteam/
│   └── run_redteam.py             Prompt injection, citation forgery, existence inference
├── tests/
│   ├── test_acl.py                 ACL predicate invariants
│   ├── test_ingestion.py           Chunking never widens access
│   ├── test_retrieval_primitives.py Tokenizer + RRF fusion
│   ├── test_tools.py               Tool validation + identity binding
│   ├── test_agent_loop.py          Bounded refinement; no retrying widens access
│   ├── test_verification.py        Independent post-retrieval permission check
│   └── test_integration_retrieval.py End-to-end: no unauthorized chunk returned
├── data/
│   ├── users_groups.json           12 personas, 6 groups
│   └── sources/                    Generated raw documents
├── indexes/                        Built artifacts (generated, not committed)
├── logs/                           audit.jsonl (generated, not committed)
├── reports/
│   ├── evaluation_report.md        Retrieval quality + permission-safety results
│   ├── redteam_report.md           Attack findings with raw counts
│   └── scale_study.md             Pre/post-filter tables and interpretation
├── Dockerfile                      Container image for the VaultSearch service
├── docker-compose.yml              Full stack: Ollama + VaultSearch (+ LocalStack optional)
├── docker-entrypoint.sh            Builds data + indexes on first container start
├── setup.sh                        Robust venv creation (handles AppImage quirk)
├── requirements.txt                Python dependencies
├── .env.example                    Environment variable reference
└── DESIGN.md                       Architecture, invariants, trade-offs, scaling path
```

## Tech stack

| Layer | Technology | Role |
|---|---|---|
| Language | Python 3.11+ | Everything |
| API service | FastAPI + Uvicorn | JSON API, static web serving, lifespan-loaded indexes |
| Keyword search | rank-bm25 (`BM25Okapi`) | Lexical ranking over the permission-filtered subset |
| Vector search | FAISS (`IndexFlatIP`) | Exact inner-product search with `IDSelectorArray` ACL filtering |
| Embeddings | sentence-transformers `all-MiniLM-L6-v2` | 384-dim normalized chunk/query vectors |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Joint query-chunk scoring of the fused shortlist |
| LLM | Ollama (default `gemma3:4b`) | Planning, sufficiency assessment, synthesis, critic — fully local |
| Agent interop | MCP (`mcp` Python SDK) | VaultSearch as permission-safe tools for external agents |
| Frontend | Plain HTML / CSS / JS | Zero-build interface served by the API process |
| Testing | pytest | ACL invariants first, then ingestion, retrieval, agent loop |
| Packaging | Docker + docker-compose | Full stack (VaultSearch + Ollama) in one command |
| Cloud layer | LocalStack + Terraform + boto3 (optional) | S3 sources, SQS-driven ingestion, DynamoDB audit |
