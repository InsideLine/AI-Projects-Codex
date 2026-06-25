from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Protocol

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
    segment_states: dict[str, dict[str, Any]] | None = None


@dataclass(frozen=True)
class SegmentSyncResult:
    table_name: str
    segment: int
    pages_read: int
    records_persisted: int
    batches_persisted: int
    last_evaluated_key: dict[str, Any] | None
    complete: bool


def build_dynamodb_client(
    region_name: str,
    profile_name: str | None = None,
    *,
    role_arn: str | None = None,
    external_id: str | None = None,
) -> DynamoDbClientProtocol:
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover
        raise DynamoDbSyncError("boto3 is required to run the DynamoDB sync job.") from exc

    session = _build_boto3_session(
        boto3,
        profile_name=profile_name,
        region_name=region_name,
        role_arn=role_arn,
        external_id=external_id,
    )
    return session.client("dynamodb", region_name=region_name)


def build_sts_client(
    region_name: str,
    profile_name: str | None = None,
    *,
    role_arn: str | None = None,
    external_id: str | None = None,
) -> Any:
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover
        raise DynamoDbSyncError("boto3 is required to resolve AWS caller identity.") from exc

    session = _build_boto3_session(
        boto3,
        profile_name=profile_name,
        region_name=region_name,
        role_arn=role_arn,
        external_id=external_id,
    )
    return session.client("sts", region_name=region_name)


def _build_boto3_session(
    boto3_module: Any,
    *,
    profile_name: str | None,
    region_name: str,
    role_arn: str | None,
    external_id: str | None,
) -> Any:
    session_kwargs: dict[str, Any] = {}
    if profile_name:
        session_kwargs["profile_name"] = profile_name
    base_session = boto3_module.session.Session(**session_kwargs)
    if not role_arn:
        return base_session

    assume_kwargs: dict[str, Any] = {
        "RoleArn": role_arn,
        "RoleSessionName": "license-violation-data-sync",
    }
    if external_id:
        assume_kwargs["ExternalId"] = external_id
    sts_client = base_session.client("sts", region_name=region_name)
    response = sts_client.assume_role(**assume_kwargs)
    credentials = response["Credentials"]
    return boto3_module.session.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=region_name,
    )


def resolve_source_account_id(
    region_name: str,
    profile_name: str | None = None,
    *,
    role_arn: str | None = None,
    external_id: str | None = None,
) -> str:
    sts_client = build_sts_client(
        region_name=region_name,
        profile_name=profile_name,
        role_arn=role_arn,
        external_id=external_id,
    )
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
    source_system: str = "aws_dynamodb",
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
                    source_system=source_system,
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


def sync_dynamodb_table_segment(
    client: DynamoDbClientProtocol,
    landing_zone: FilesystemLandingZone,
    *,
    table_name: str,
    source_account: str,
    segment: int,
    total_segments: int,
    source_system: str = "aws_dynamodb",
    page_limit: int = 1000,
    max_pages: int | None = None,
    start_key: dict[str, Any] | None = None,
) -> SegmentSyncResult:
    if page_limit <= 0:
        raise DynamoDbSyncError("page_limit must be positive.")
    if total_segments <= 1:
        raise DynamoDbSyncError("total_segments must be greater than 1 for parallel scans.")
    if segment < 0 or segment >= total_segments:
        raise DynamoDbSyncError("segment must be between 0 and total_segments - 1.")

    pages_read = 0
    records_persisted = 0
    batches_persisted = 0
    next_key = start_key
    extracted_at = datetime.now(timezone.utc)

    while True:
        if max_pages is not None and pages_read >= max_pages:
            return SegmentSyncResult(
                table_name=table_name,
                segment=segment,
                pages_read=pages_read,
                records_persisted=records_persisted,
                batches_persisted=batches_persisted,
                last_evaluated_key=next_key,
                complete=False,
            )

        scan_kwargs: dict[str, Any] = {
            "TableName": table_name,
            "Limit": page_limit,
            "Segment": segment,
            "TotalSegments": total_segments,
        }
        if next_key:
            scan_kwargs["ExclusiveStartKey"] = next_key

        response = client.scan(**scan_kwargs)
        items = response.get("Items") or []
        last_evaluated_key = response.get("LastEvaluatedKey")
        pages_read += 1

        if items:
            landing_zone.persist(
                RawBatch(
                    source_system=source_system,
                    dataset=table_name,
                    records=tuple(items),
                    extracted_at=extracted_at,
                    source_account=source_account,
                    schema_version="dynamodb-attribute-json-v1",
                    cursor=json.dumps(last_evaluated_key, sort_keys=True) if last_evaluated_key else None,
                    notes=json.dumps(
                        {
                            "count": response.get("Count"),
                            "page_number": pages_read,
                            "scanned_count": response.get("ScannedCount"),
                            "segment": segment,
                            "total_segments": total_segments,
                        },
                        sort_keys=True,
                    ),
                )
            )
            batches_persisted += 1
            records_persisted += len(items)

        next_key = last_evaluated_key
        if not next_key:
            return SegmentSyncResult(
                table_name=table_name,
                segment=segment,
                pages_read=pages_read,
                records_persisted=records_persisted,
                batches_persisted=batches_persisted,
                last_evaluated_key=None,
                complete=True,
            )


def sync_dynamodb_table_parallel(
    client: DynamoDbClientProtocol,
    landing_zone: FilesystemLandingZone,
    *,
    table_name: str,
    source_account: str,
    total_segments: int,
    source_system: str = "aws_dynamodb",
    page_limit: int = 1000,
    max_pages: int | None = None,
    start_keys: dict[int, dict[str, Any] | None] | None = None,
    on_segment_complete: Callable[[SegmentSyncResult], None] | None = None,
) -> TableSyncResult:
    if total_segments <= 1:
        raise DynamoDbSyncError("total_segments must be greater than 1 for parallel scans.")

    segment_results: list[SegmentSyncResult] = []
    with ThreadPoolExecutor(max_workers=total_segments) as executor:
        future_map = {
            executor.submit(
                sync_dynamodb_table_segment,
                client,
                landing_zone,
                table_name=table_name,
                source_account=source_account,
                segment=segment,
                total_segments=total_segments,
                source_system=source_system,
                page_limit=page_limit,
                max_pages=max_pages,
                start_key=(start_keys or {}).get(segment),
            ): segment
            for segment in range(total_segments)
        }
        for future in as_completed(future_map):
            result = future.result()
            segment_results.append(result)
            if on_segment_complete is not None:
                on_segment_complete(result)

    segment_results.sort(key=lambda result: result.segment)
    segment_states = {
        str(result.segment): {
            "last_evaluated_key": result.last_evaluated_key,
            "complete": result.complete,
            "pages_read": result.pages_read,
            "records_persisted": result.records_persisted,
            "batches_persisted": result.batches_persisted,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        for result in segment_results
    }

    return TableSyncResult(
        table_name=table_name,
        pages_read=sum(result.pages_read for result in segment_results),
        records_persisted=sum(result.records_persisted for result in segment_results),
        batches_persisted=sum(result.batches_persisted for result in segment_results),
        last_evaluated_key=None,
        complete=all(result.complete for result in segment_results),
        segment_states=segment_states,
    )


def update_checkpoint_for_result(
    checkpoints: dict[str, dict[str, Any]],
    result: TableSyncResult,
) -> dict[str, dict[str, Any]]:
    payload = {
        "complete": result.complete,
        "pages_read": result.pages_read,
        "records_persisted": result.records_persisted,
        "batches_persisted": result.batches_persisted,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if result.segment_states is not None:
        payload["mode"] = "parallel_scan"
        payload["segments"] = result.segment_states
        payload["last_evaluated_key"] = None
    else:
        payload["mode"] = "scan"
        payload["last_evaluated_key"] = result.last_evaluated_key
    checkpoints[result.table_name] = payload
    return checkpoints
