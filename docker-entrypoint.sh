#!/bin/sh
set -eu

# Wait for Ollama to be reachable before building indexes or starting the
# server. In Docker Compose the `depends_on` condition ensures ollama-pull
# has completed, but a brief extra health poll costs nothing and prevents
# confusing "connection refused" errors on slow machines.
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
MAX_WAIT=120
WAITED=0
echo "Waiting for Ollama at $OLLAMA_URL ..."
until wget -qO- "$OLLAMA_URL/api/tags" >/dev/null 2>&1; do
  if [ "$WAITED" -ge "$MAX_WAIT" ]; then
    echo "Ollama did not become ready after ${MAX_WAIT}s — starting anyway."
    break
  fi
  sleep 3
  WAITED=$((WAITED + 3))
done
echo "Ollama ready."

# Generate synthetic data on first run.
if [ ! -f data/chunks.json ]; then
  echo "Building synthetic corpus..."
  python ingestion/generate_data.py
  python ingestion/ingest.py
fi

# Build indexes on first run (or if they are missing).
if [ ! -f indexes/vectors.faiss ] || [ ! -f indexes/bm25.pkl ]; then
  echo "Building search indexes..."
  python indexing/build_indexes.py
fi

exec uvicorn app.api:app --host 0.0.0.0 --port 8000
