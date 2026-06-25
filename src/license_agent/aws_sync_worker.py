from __future__ import annotations

import json
from typing import Any

from .dynamodb_sync import (
    SegmentSyncResult,
    TableSyncResult,
    build_dynamodb_client,
    resolve_source_account_id,
    sync_dynamodb_table,
    sync_dynamodb_table_parallel,
)
from .ingest import build_landing_zone
from .settings import LicenseAgentSettings


def run_weekly_dynamodb_sync(settings: LicenseAgentSettings | None = None) -> dict[str, Any]:
    settings = settings or LicenseAgentSettings.from_env(enable_secret_fallback=False)
    landing_zone = build_landing_zone(settings)
    client = build_dynamodb_client(
        region_name=settings.aws_region,
        profile_name=settings.aws_profile,
        role_arn=settings.dynamodb_source_role_arn,
        external_id=settings.dynamodb_source_external_id,
    )
    source_account = resolve_source_account_id(
        region_name=settings.aws_region,
        profile_name=settings.aws_profile,
        role_arn=settings.dynamodb_source_role_arn,
        external_id=settings.dynamodb_source_external_id,
    )
    tables = tuple(
        table.strip()
        for table in settings.dynamodb_source_tables.split(",")
        if table.strip()
    )
    summary: dict[str, Any] = {
        "source_account": source_account,
        "source_system": settings.dynamodb_source_system,
        "landing_zone": landing_zone.health(),
        "tables": [],
    }
    for table_name in tables:
        result = _sync_one_table(settings, client, landing_zone, source_account, table_name)
        summary["tables"].append(_result_summary(result))
    return summary


def _sync_one_table(
    settings: LicenseAgentSettings,
    client: Any,
    landing_zone: Any,
    source_account: str,
    table_name: str,
) -> TableSyncResult:
    if settings.dynamodb_parallel_segments > 1:
        return sync_dynamodb_table_parallel(
            client,
            landing_zone,
            table_name=table_name,
            source_account=source_account,
            total_segments=settings.dynamodb_parallel_segments,
            source_system=settings.dynamodb_source_system,
            page_limit=settings.dynamodb_scan_page_limit,
            on_segment_complete=_log_segment,
        )
    return sync_dynamodb_table(
        client,
        landing_zone,
        table_name=table_name,
        source_account=source_account,
        source_system=settings.dynamodb_source_system,
        page_limit=settings.dynamodb_scan_page_limit,
    )


def _log_segment(segment_result: SegmentSyncResult) -> None:
    print(
        json.dumps(
            {
                "event": "dynamodb_segment_complete",
                "table_name": segment_result.table_name,
                "segment": segment_result.segment,
                "complete": segment_result.complete,
                "pages_read": segment_result.pages_read,
                "records_persisted": segment_result.records_persisted,
            },
            sort_keys=True,
        )
    )


def _result_summary(result: TableSyncResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "table_name": result.table_name,
        "complete": result.complete,
        "pages_read": result.pages_read,
        "records_persisted": result.records_persisted,
        "batches_persisted": result.batches_persisted,
    }
    if result.segment_states:
        payload["segment_count"] = len(result.segment_states)
    return payload


def main() -> None:
    print(json.dumps(run_weekly_dynamodb_sync(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
