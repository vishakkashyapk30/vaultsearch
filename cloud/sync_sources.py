"""Upload the raw connector documents to the S3 sources bucket.

Each upload fires an s3:ObjectCreated event into the vaultsearch-ingest SQS
queue (wired by cloud/main.tf), which the ingest worker consumes to rebuild
the indexes — the same event-driven pattern a production connector would use.

Run:  python cloud/sync_sources.py
Env:  AWS_ENDPOINT_URL (default http://localhost:4566 for LocalStack)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = ROOT / "data" / "sources"
BUCKET = os.getenv("SOURCES_BUCKET", "vaultsearch-sources")

os.environ.setdefault("AWS_ENDPOINT_URL", "http://localhost:4566")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


def main() -> None:
    if not SOURCES_DIR.exists():
        sys.exit("data/sources not found; run ingestion/generate_data.py first")
    s3 = boto3.client("s3")
    files = sorted(SOURCES_DIR.glob("*.json"))
    for path in files:
        key = f"sources/{path.name}"
        s3.upload_file(str(path), BUCKET, key)
        print(f"uploaded s3://{BUCKET}/{key} ({path.stat().st_size} bytes)")
    print(f"done: {len(files)} source file(s)")


if __name__ == "__main__":
    main()
