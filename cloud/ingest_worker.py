"""Event-driven ingest worker: SQS → download sources → rebuild → publish.

Long-polls the vaultsearch-ingest queue. When S3 object-created events arrive
(from cloud/sync_sources.py or any future connector), it downloads the source
files, runs the existing ingestion and indexing pipeline unchanged, and
uploads the built artifacts to the artifacts bucket. ACLs travel inside the
source documents, so this worker cannot widen access any more than the local
pipeline can.

Run:  python cloud/ingest_worker.py [--once]
Env:  AWS_ENDPOINT_URL (default http://localhost:4566 for LocalStack)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = ROOT / "data" / "sources"
INDEX_DIR = ROOT / "indexes"

SOURCES_BUCKET = os.getenv("SOURCES_BUCKET", "vaultsearch-sources")
ARTIFACTS_BUCKET = os.getenv("ARTIFACTS_BUCKET", "vaultsearch-artifacts")
QUEUE_NAME = os.getenv("INGEST_QUEUE", "vaultsearch-ingest")

os.environ.setdefault("AWS_ENDPOINT_URL", "http://localhost:4566")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


def object_keys(message_body: str) -> list[str]:
    """Extract S3 object keys from an S3→SQS event notification body."""
    try:
        body = json.loads(message_body)
        return [
            record["s3"]["object"]["key"]
            for record in body.get("Records", [])
            if record.get("eventName", "").startswith("ObjectCreated")
        ]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def rebuild(s3, keys: list[str]) -> None:
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    for key in keys:
        target = SOURCES_DIR / Path(key).name
        s3.download_file(SOURCES_BUCKET, key, str(target))
        print(f"downloaded s3://{SOURCES_BUCKET}/{key}")

    for script in ("ingestion/ingest.py", "indexing/build_indexes.py"):
        print(f"running {script} ...")
        subprocess.run([sys.executable, str(ROOT / script)], check=True, cwd=ROOT)

    for name in ("bm25.pkl", "vectors.faiss", "chunks_meta.json"):
        s3.upload_file(str(INDEX_DIR / name), ARTIFACTS_BUCKET, f"indexes/{name}")
        print(f"published s3://{ARTIFACTS_BUCKET}/indexes/{name}")


def main() -> None:
    once = "--once" in sys.argv
    s3 = boto3.client("s3")
    sqs = boto3.client("sqs")
    queue_url = sqs.get_queue_url(QueueName=QUEUE_NAME)["QueueUrl"]
    print(f"polling {queue_url} (Ctrl+C to stop)")

    while True:
        response = sqs.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=10
        )
        messages = response.get("Messages", [])
        keys: list[str] = []
        for message in messages:
            keys.extend(object_keys(message["Body"]))
            sqs.delete_message(
                QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"]
            )
        if keys:
            rebuild(s3, sorted(set(keys)))
            print("rebuild complete")
        elif once:
            print("queue empty")
        if once:
            break


if __name__ == "__main__":
    main()
