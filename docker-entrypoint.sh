#!/bin/sh
set -eu

if [ ! -f data/chunks.json ]; then
  python ingestion/generate_data.py
  python ingestion/ingest.py
fi

if [ ! -f indexes/vectors.faiss ] || [ ! -f indexes/bm25.pkl ]; then
  python indexing/build_indexes.py
fi

exec uvicorn app.api:app --host 0.0.0.0 --port 8000
