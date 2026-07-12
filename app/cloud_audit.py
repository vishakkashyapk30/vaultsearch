"""Optional DynamoDB audit sink (LocalStack or real AWS).

Activated by setting AUDIT_DYNAMODB_TABLE. The local JSONL audit log always
remains the primary record; DynamoDB writes are best-effort so a cloud outage
can never take down or block the ask path.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("vaultsearch.cloud_audit")


class DynamoAuditSink:
    def __init__(self, table_name: str):
        import boto3  # optional dependency: cloud/requirements.txt

        # boto3 honors AWS_ENDPOINT_URL, so the same code targets LocalStack
        # (http://localhost:4566) or real AWS with no branching here.
        self.table = boto3.resource("dynamodb").Table(table_name)

    def write(self, event: dict) -> None:
        try:
            timestamp = datetime.now(timezone.utc).isoformat()
            self.table.put_item(
                Item={
                    "user_id": event.get("user_id", "unknown"),
                    "event_key": f"{timestamp}#{uuid.uuid4().hex[:8]}",
                    "event": json.dumps(event, separators=(",", ":")),
                }
            )
        except Exception as exc:  # noqa: BLE001 - audit mirror must never break /ask
            logger.warning("DynamoDB audit write failed: %s", exc)


def make_audit_sink() -> DynamoAuditSink | None:
    table_name = os.getenv("AUDIT_DYNAMODB_TABLE", "").strip()
    if not table_name:
        return None
    try:
        return DynamoAuditSink(table_name)
    except Exception as exc:  # noqa: BLE001 - boto3 missing or misconfigured
        logger.warning("DynamoDB audit sink disabled: %s", exc)
        return None
