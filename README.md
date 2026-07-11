# VaultSearch

VaultSearch is a local, permission-aware enterprise search and answer service.
It combines BM25 and dense retrieval, Reciprocal Rank Fusion, cross-encoder
reranking, deterministic ACL enforcement, and Ollama-based query planning and
cited answer synthesis.

The project requires no paid cloud account or API. All retrieval models run
locally, and the agent uses an existing local Ollama installation.

## Current results

- 220 synthetic documents from Slack-like, Drive-like, and ticketing sources
- 508 indexed chunks with inherited source ACLs
- 0 permission leaks across 100 adversarial retrieval attempts
- Hybrid + reranker: NDCG@10 0.865, MRR 0.792, p50 38.9 ms

See `reports/evaluation_report.md` for the full BM25/vector/hybrid comparison.

## Security model

Every document and chunk carries an `allowed_principals` ACL. At query time:

1. the user is expanded to their user and group principals;
2. BM25 and FAISS are restricted to authorized chunk IDs before scoring;
3. only authorized candidates reach fusion and reranking;
4. an independent deterministic verifier checks every chunk again before the
   LLM sees it;
5. empty ACLs and unknown users are denied by default; admins are explicit.

The LLM never decides authorization.

## Local setup

Requirements: Python 3.11+, Ollama, and roughly 2 GB for local model weights.
The default agent model is `gemma3:4b`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

ollama serve                 # only if it is not already running
ollama pull gemma3:4b        # only if the model is not already installed

python ingestion/generate_data.py
python ingestion/ingest.py
python indexing/build_indexes.py
uvicorn app.api:app --host 0.0.0.0 --port 8000
```

Ask a permitted finance user:

```bash
curl -s http://localhost:8000/ask \
  -H 'content-type: application/json' \
  -d '{"user_id":"user:dmitri","question":"What is the Q3 infrastructure budget?"}'
```

Try the same question as an engineer (`user:asha`): the finance-only evidence
never reaches ranking or generation.

Configuration:

- `OLLAMA_MODEL` (default `gemma3:4b`)
- `OLLAMA_URL` (default `http://127.0.0.1:11434`)
- `USE_RERANKER` (`true` by default)

OpenAPI documentation is available at `http://localhost:8000/docs`. Structured
audit events are appended to `logs/audit.jsonl`.

## Tests and evaluation

```bash
python -m pytest -q
python eval/evaluate.py
```

Tests cover deny-by-default behavior, user/group expansion, admin access,
chunk ACL inheritance, RRF, independent verification, and end-to-end
retrieval isolation.

## Docker

With Ollama running on the host:

```bash
docker compose up --build
```

The container builds missing data/index artifacts on startup. The first run
downloads the embedding and reranker weights; subsequent runs reuse the
mounted Hugging Face cache.

## Repository map

- `ingestion/`: deterministic corpus generation, normalization, and chunking
- `indexing/`: BM25 and FAISS index construction
- `app/acl.py`: identity expansion and authorization primitive
- `app/retrieval_core.py`: ACL-prefiltered hybrid retrieval
- `app/agents.py`: planning, verification, and synthesis
- `app/api.py`: FastAPI service and structured audit logging
- `eval/`: retrieval, latency, and adversarial permission evaluation
- `tests/`: unit and integration tests
- `DESIGN.md`: architecture, invariants, trade-offs, and scaling path
