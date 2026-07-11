<div align="center">

# VaultSearch

**A secure retrieval boundary for RAG — the answer never surfaces what you can't see.**

Ask questions over a company's documents and get a cited answer, where every
user only ever sees what their permissions allow — enforced *before* search
runs, not patched on afterward. Runs entirely on your own machine.

</div>

---

## Why this project exists

The moment you put a language model in front of a company's documents, you
inherit an access-control problem that most retrieval-augmented generation
(RAG) demos quietly ignore.

A normal RAG pipeline embeds every document, retrieves the most relevant chunks
for a question, and feeds them to a model to write an answer. That's fine when
everyone is allowed to read everything. Inside a real organization they are
not: finance salaries, an unannounced acquisition, an HR investigation, a
security postmortem — each is readable by some people and off-limits to others.
If the retriever pulls a restricted chunk into the model's context, that
content can end up in the answer, in a citation, or leak indirectly through
phrasing. **One leaked sentence is a breach.** And a language model cannot be
trusted to keep a secret you handed it: if the restricted text is in the
prompt, "please don't mention the acquisition" is one clever question away from
failing.

The only robust fix is to make sure unauthorized content is **never retrieved
in the first place**, and to treat everything the model does afterward as
untrusted. That is a general problem — it applies to every internal copilot,
support bot, and multi-tenant RAG product, not to any one company. VaultSearch
is a small, complete, runnable reference implementation of that secure
boundary, with enterprise search as the demo scenario.

## What it does

- **Permission-aware hybrid search.** Combines keyword search (BM25) and
  semantic vector search, fuses the rankings, and reranks with a cross-encoder
  — all restricted to documents the asking user is allowed to read.
- **A cited, grounded answer.** A locally hosted language model (via
  [Ollama](https://ollama.com)) plans the search, then writes an answer that
  cites the specific documents it used. If there's no permitted evidence, it
  says so instead of guessing.
- **Defense in depth.** Permissions are checked before search, verified again
  before the answer is written, and any citation the model invents is stripped
  from the response.
- **A real interface.** A clickable web app where you switch identities and
  watch the same question return different answers, because the evidence
  changes first.
- **Honest evaluation.** Retrieval quality, latency, an adversarial red-team of
  the language-model layer, and a scaling study — with the failures reported,
  not hidden.

## See it in one picture

Ask *"What is the Q3 infrastructure budget?"* as a finance user and you get the
number, cited. Ask the exact same question as an engineer and you get *"I could
not find permitted evidence"* — because the finance documents were removed from
the candidate set before the search ran. The model was never even given the
chance to leak them.

---

## Quick start (for absolute beginners)

You do **not** need any cloud account, API key, or paid service. Everything
runs on your computer. Total time: about 10 minutes, most of it waiting for
downloads.

### Step 1 — Install the three things you need

1. **Python 3.11 or newer.** Check by opening a terminal and running:
   ```bash
   python3 --version
   ```
   If it prints a version number ≥ 3.11, you're set. Otherwise install it from
   [python.org](https://www.python.org/downloads/).

2. **Ollama** — this runs the language model locally, for free. Download it
   from [ollama.com/download](https://ollama.com/download), install it, then in
   a terminal download a small model:
   ```bash
   ollama pull gemma3:4b
   ```
   (This is a ~3 GB download. `llama3.2` or `mistral` also work — see
   Configuration below.)

3. **Git** (to download this project), from
   [git-scm.com](https://git-scm.com/downloads).

### Step 2 — Download the project

```bash
git clone https://github.com/vishakkashyapk30/vaultsearch.git
cd vaultsearch
```

### Step 3 — Set up Python and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

The first install downloads PyTorch and some models and can take several
minutes. This is normal.

> **Using Cursor as a Linux AppImage?** If `venv` tries to execute
> `cursor.AppImage` and exits with `SIGTRAP`, Cursor's `APPIMAGE` environment
> variable is confusing Python. Run `unset APPIMAGE`, remove the partial
> `.venv`, and repeat Step 3:
> ```bash
> unset APPIMAGE
> rm -rf .venv
> python3 -m venv .venv
> source .venv/bin/activate
> pip install -r requirements.txt
> ```

### Step 4 — Build the demo data and search index

This creates a synthetic company (people, groups, and ~225 documents across
chat, docs, and tickets), then builds the search indexes:

```bash
python ingestion/generate_data.py
python ingestion/ingest.py
python indexing/build_indexes.py
```

### Step 5 — Start it and open the app

Make sure Ollama is running (it usually starts on install; if not, run
`ollama serve` in a separate terminal), then:

```bash
uvicorn app.api:app --port 8000
```

Open **http://localhost:8000** in your browser. Pick a person on the left, ask
a question, and try the same question as someone from a different team.

> **First question is slow?** The very first request loads the models into
> memory and the LLM warms up. Later questions are much faster.

---

## Using the app

- **Ask tab** — Choose an identity in the left sidebar (each card shows how much
  of the corpus that person can see). Ask a question and you'll get an answer
  with clickable citations, the exact evidence the model was allowed to read,
  and a step-by-step trace showing how many documents were excluded by
  permissions before search even ran.
- **Compare retrieval tab** — Enter a query and see keyword, semantic, hybrid,
  and reranked results side by side — all over the same permission-filtered set
  — so you can see what each stage contributes.
- **How it works tab** — A plain-language explanation of the security model.

Things worth trying:

- Ask *"What is the Q3 infrastructure budget?"* as **Dmitri (finance)** and then
  as **Asha (engineering)**.
- Ask *"What is the Project Hawk offer range?"* as **Elena (leadership)** vs
  **Ines (all-staff only)**.
- In Compare, search *"latency"* and watch reranking reorder the hits.

---

## How the security model works

For a request `(user, question)`:

1. **Expand identity → principals.** The user becomes their own ID plus every
   group they belong to.
2. **Filter before search.** Both BM25 and vector search are restricted to
   chunks whose access list intersects those principals. Restricted text never
   enters ranking, reranking, or the model's prompt.
3. **Fuse and rerank** the authorized candidates (Reciprocal Rank Fusion, then
   a cross-encoder reranker).
4. **Verify again.** An independent check re-confirms every retrieved chunk
   against the user's permissions before anything reaches the model — defense
   in depth in case retrieval ever regresses.
5. **Synthesize and sanitize.** The model answers using only verified evidence;
   any citation it invents that doesn't map to authorized evidence is stripped
   before the response is returned.

**The authorization decision is always deterministic code. The language model
plans searches and phrases answers; it is never trusted to decide who can see
what.** Empty access lists are denied by default (admin-only); unknown users
see nothing.

See [`DESIGN.md`](DESIGN.md) for the architecture, trade-offs, and scaling path.

---

## Results

All numbers are reproducible with the scripts in `eval/` and `redteam/`.

### Retrieval quality and latency (`reports/evaluation_report.md`)

| Mode | NDCG@10 | MRR | p50 latency | p95 latency |
|---|---:|---:|---:|---:|
| Keyword (BM25) | 0.783 | 0.708 | 1.4 ms | 4.4 ms |
| Semantic (vector) | 0.853 | 0.792 | 5.1 ms | 403 ms |
| Hybrid (RRF) | 0.843 | 0.750 | 7.3 ms | 19.5 ms |
| **Hybrid + reranker** | **0.865** | **0.792** | 50 ms | 81 ms |

Permission safety: **0 leaks across 100 adversarial retrieval attempts**, 0
unauthorized chunks returned.

### Red-team of the language-model layer (`reports/redteam_report.md`)

This attacks the layer *above* retrieval and reports what actually happens:

| Attack | Real breaches | Honest finding |
|---|---:|---|
| Prompt-injection exfiltration | 0 | Hostile instructions planted in readable docs can't leak other teams' secrets, because those docs are never retrieved into context. |
| Citation forgery | 0 | The local model **did** fabricate citations in 10 of 24 raw responses — all were stripped by sanitization before reaching the user. |
| Existence inference | 0 | A restricted-but-hidden topic is indistinguishable from a topic that doesn't exist. |

The security-critical counts are zero because authorization lives in
deterministic code below the model. The non-zero findings (the model forging
citations) are real, observed model-quality problems that the grounding and
sanitization layers contain.

### Scaling study (`reports/scale_study.md`)

Pre-filtering vs. post-filtering permission-aware vector search, up to **1
million vectors**. The headline: post-filtering with a fixed over-fetch budget
**silently loses correct results** once a user can see only a small slice of the
corpus (recall drops to ~0.45 at 0.2% visibility), while pre-filtering stays
exact *and* gets faster as permissions tighten. Under the selective access that
is normal in an enterprise, pre-filtering wins on both correctness and latency.

---

## Running the evaluations yourself

```bash
python -m pytest -q            # unit + integration tests (ACL logic first)
python eval/evaluate.py        # retrieval quality, latency, permission leakage
python redteam/run_redteam.py  # LLM-layer red-team (needs Ollama running)
python eval/scale_study.py     # pre/post-filter scaling (no models needed)
```

## Run with Docker

With Ollama running on the host:

```bash
docker compose up --build
```

Then open http://localhost:8000. The container builds the data and indexes on
first start and reuses a mounted model cache afterward. It is a standard
container and deploys unchanged to any cloud runtime — no cloud account is
needed to run it locally.

## Configuration

Set these as environment variables before starting the server:

- `OLLAMA_MODEL` — which local model to use (default `gemma3:4b`).
- `OLLAMA_URL` — Ollama endpoint (default `http://127.0.0.1:11434`).
- `USE_RERANKER` — set to `false` to skip the cross-encoder for lower latency.

## Project layout

```
app/            core service
  acl.py            identity expansion + authorization (the security primitive)
  retrieval_core.py permission-prefiltered hybrid retrieval (BM25 + FAISS + RRF + rerank)
  agents.py         query planning, independent verification, cited synthesis
  ollama_client.py  minimal local LLM client
  api.py            FastAPI service + web UI + audit logging
web/            clickable interface (no build step, plain HTML/CSS/JS)
ingestion/      synthetic corpus generation, normalization, chunking
indexing/       BM25 + FAISS index builders
eval/           retrieval metrics, latency, adversarial permission tests, scale study
redteam/        prompt-injection / citation-forgery / existence-inference study
tests/          unit + integration tests
reports/        generated evaluation, red-team, and scaling reports
DESIGN.md       architecture, invariants, trade-offs, scaling path
```

## Tech stack

Python · FastAPI · FAISS · rank-bm25 · sentence-transformers (MiniLM embeddings
+ cross-encoder reranker) · Ollama · pytest · Docker. No paid services.
