from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .ingest import FilesystemLandingZone, RawBatch


class DynamoDbSyncError(RuntimeError):
    pass


class DynamoDbClientProtocol(Protocol):
    def scan(self, **kwargs: Any) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class TableSyncResult:
    table_name: str
    pages_read: int
    records_persisted: int
    batches_persisted: int
    last_evaluated_key: dict[str, Any] | None
    complete: bool


def build_dynamodb_client(region_name: str, profile_name: str | None = None) -> DynamoDbClientProtocol:
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover
        raise DynamoDbSyncError("boto3 is required to run the DynamoDB sync job.") from exc

    session_kwargs: dict[str, Any] = {}
    if profile_name:
        session_kwargs["profile_name"] = profile_name
    session = boto3.session.Session(**session_kwargs)
    return session.client("dynamodb", region_name=region_name)


def build_sts_client(region_name: str, profile_name: str | None = None) -> Any:
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover
        raise DynamoDbSyncError("boto3 is required to resolve AWS caller identity.") from exc

    session_kwargs: dict[str, Any] = {}
    if profile_name:
        session_kwargs["profile_name"] = profile_name
    session = boto3.session.Session(**session_kwargs)
    return session.client("sts", region_name=region_name)


def resolve_source_account_id(region_name: str, profile_name: str | None = None) -> str:
    sts_client = build_sts_client(region_name=region_name, profile_name=profile_name)
    response = sts_client.get_caller_identity()
    account_id = response.get("Account")
    if not isinstance(account_id, str) or not account_id:
        raise DynamoDbSyncError("Unable to resolve AWS source account ID from STS.")
    return account_id


def load_checkpoints(path: str | Path) -> dict[str, dict[str, Any]]:
    checkpoint_path = Path(path)
    if not checkpoint_path.exists():
        return {}
    payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise DynamoDbSyncError("Checkpoint file must contain a JSON object.")
    return payload


def save_checkpoints(path: str | Path, checkpoints: dict[str, dict[str, Any]]) -> None:
    checkpoint_path = Path(path)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(json.dumps(checkpoints, indent=2, sort_keys=True), encoding="utf-8")


def sync_dynamodb_table(
    client: DynamoDbClientProtocol,
    landing_zone: FilesystemLandingZone,
    *,
    table_name: str,
    source_account: str,
    page_limit: int = 1000,
    max_pages: int | None = None,
    start_key: dict[str, Any] | None = None,
) -> TableSyncResult:
    if page_limit <= 0:
        raise DynamoDbSyncError("page_limit must be positive.")

    pages_read = 0
    records_persisted = 0
    batches_persisted = 0
    next_key = start_key
    extracted_at = datetime.now(timezone.utc)

    while True:
        if max_pages is not None and pages_read >= max_pages:
            return TableSyncResult(
                table_name=table_name,
                pages_read=pages_read,
                records_persisted=records_persisted,
                batches_persisted=batches_persisted,
                last_evaluated_key=next_key,
                complete=False,
            )

        scan_kwargs: dict[str, Any] = {"TableName": table_name, "Limit": page_limit}
        if next_key:
            scan_kwargs["ExclusiveStartKey"] = next_key

        response = client.scan(**scan_kwargs)
        items = response.get("Items") or []
        last_evaluated_key = response.get("LastEvaluatedKey")
        pages_read += 1

        if items:
            landing_zone.persist(
                RawBatch(
                    source_system="aws_dynamodb",
                    dataset=table_name,
                    records=tuple(items),
                    extracted_at=extracted_at,
                    source_account=source_account,
                    schema_version="dynamodb-attribute-json-v1",
                    cursor=json.dumps(last_evaluated_key, sort_keys=True) if last_evaluated_key else None,
                    notes=json.dumps(
                        {
                            "count": response.get("Count"),
                            "scanned_count": response.get("ScannedCount"),
                        },
                        sort_keys=True,
                    ),
                )
            )
            batches_persisted += 1
            records_persisted += len(items)

        next_key = last_evaluated_key
        if not next_key:
            return TableSyncResult(
                table_name=table_name,
                pages_read=pages_read,
                records_persisted=records_persisted,
                batches_persisted=batches_persisted,
                last_evaluated_key=None,
                complete=True,
            )


def update_checkpoint_for_result(
    checkpoints: dict[str, dict[str, Any]],
    result: TableSyncResult,
) -> dict[str, dict[str, Any]]:
    checkpoints[result.table_name] = {
        "last_evaluated_key": result.last_evaluated_key,
        "complete": result.complete,
        "pages_read": result.pages_read,
        "records_persisted": result.records_persisted,
        "batches_persisted": result.batches_persisted,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return checkpoints

