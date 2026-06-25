from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from license_agent.dynamodb_sync import (
    build_dynamodb_client,
    load_checkpoints,
    resolve_source_account_id,
    SegmentSyncResult,
    TableSyncResult,
    save_checkpoints,
    sync_dynamodb_table,
    sync_dynamodb_table_parallel,
    update_checkpoint_for_result,
)
from license_agent.ingest import build_landing_zone
from license_agent.settings import LicenseAgentSettings, load_dotenv


DEFAULT_TABLES = ("ProcessInfo", "SiteInfo", "TenantInfo")


def parse_args() -> argparse.Namespace:
    default_tables = tuple(
        table.strip()
        for table in os.getenv("DYNAMODB_SOURCE_TABLES", ",".join(DEFAULT_TABLES)).split(",")
        if table.strip()
    )
    parser = argparse.ArgumentParser(description="Pull DynamoDB tables into the raw landing zone.")
    parser.add_argument("--env-file", type=str, default=None, help="Optional env file to load before running.")
    parser.add_argument(
        "--table",
        action="append",
        dest="tables",
        help="Repeatable table name. Defaults to ProcessInfo, SiteInfo, TenantInfo.",
    )
    parser.add_argument(
        "--page-limit",
        type=int,
        default=int(os.getenv("DYNAMODB_SCAN_PAGE_LIMIT", "250")),
        help="DynamoDB scan page size.",
    )
    parser.add_argument("--max-pages", type=int, default=None, help="Optional maximum pages per table for a single run.")
    parser.add_argument(
        "--checkpoint-path",
        type=str,
        default=os.getenv("DYNAMODB_CHECKPOINT_PATH", "local_data/checkpoints/dynamodb_sync.json"),
        help="Where to store sync resume checkpoints.",
    )
    parser.add_argument(
        "--parallel-segments",
        type=int,
        default=int(os.getenv("DYNAMODB_PARALLEL_SEGMENTS", "1")),
        help="Use DynamoDB parallel scan when greater than 1.",
    )
    parser.add_argument(
        "--source-system",
        type=str,
        default=os.getenv("DYNAMODB_SOURCE_SYSTEM", "aws_dynamodb"),
        help="Source system label written into raw landing manifests.",
    )
    parser.add_argument(
        "--reset-checkpoints",
        action="store_true",
        help="Ignore existing checkpoints and start from the beginning of each table.",
    )
    parsed = parser.parse_args()
    if not parsed.tables:
        parsed.tables = list(default_tables)
    return parsed


def main() -> None:
    args = parse_args()
    if args.env_file:
        load_dotenv(args.env_file)
    else:
        auto_env = find_default_env_file()
        if auto_env:
            load_dotenv(auto_env)

    settings = LicenseAgentSettings.from_env(enable_secret_fallback=False)
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

    checkpoint_path = Path(args.checkpoint_path)
    checkpoints = {} if args.reset_checkpoints else load_checkpoints(checkpoint_path)
    tables = tuple(args.tables)

    summary: dict[str, object] = {
        "source_account": source_account,
        "region": settings.aws_region,
        "landing_zone": landing_zone.health(),
        "parallel_segments": args.parallel_segments,
        "source_system": args.source_system,
        "tables": [],
    }

    for table_name in tables:
        checkpoint = checkpoints.get(table_name)
        if args.parallel_segments > 1:
            start_keys = build_parallel_start_keys(checkpoint, args.parallel_segments)
            completed_segments = build_parallel_segment_states(checkpoint)

            def on_segment_complete(segment_result: SegmentSyncResult) -> None:
                completed_segments[str(segment_result.segment)] = segment_state_from_result(segment_result)
                partial_result = TableSyncResult(
                    table_name=table_name,
                    pages_read=sum(
                        int(state.get("pages_read", 0))
                        for state in completed_segments.values()
                        if isinstance(state, dict)
                    ),
                    records_persisted=sum(
                        int(state.get("records_persisted", 0))
                        for state in completed_segments.values()
                        if isinstance(state, dict)
                    ),
                    batches_persisted=sum(
                        int(state.get("batches_persisted", 0))
                        for state in completed_segments.values()
                        if isinstance(state, dict)
                    ),
                    last_evaluated_key=None,
                    complete=len(completed_segments) == args.parallel_segments
                    and all(
                        isinstance(state, dict) and bool(state.get("complete"))
                        for state in completed_segments.values()
                    ),
                    segment_states=dict(sorted(completed_segments.items())),
                )
                update_checkpoint_for_result(checkpoints, partial_result)
                save_checkpoints(checkpoint_path, checkpoints)

            result = sync_dynamodb_table_parallel(
                client,
                landing_zone,
                table_name=table_name,
                source_account=source_account,
                total_segments=args.parallel_segments,
                source_system=args.source_system,
                page_limit=args.page_limit,
                max_pages=args.max_pages,
                start_keys=start_keys,
                on_segment_complete=on_segment_complete,
            )
        else:
            start_key = None
            if checkpoint and isinstance(checkpoint, dict):
                start_key = checkpoint.get("last_evaluated_key")

            result = sync_dynamodb_table(
                client,
                landing_zone,
                table_name=table_name,
                source_account=source_account,
                source_system=args.source_system,
                page_limit=args.page_limit,
                max_pages=args.max_pages,
                start_key=start_key,
            )
        update_checkpoint_for_result(checkpoints, result)
        save_checkpoints(checkpoint_path, checkpoints)
        summary_entry = {
            "table_name": result.table_name,
            "pages_read": result.pages_read,
            "records_persisted": result.records_persisted,
            "batches_persisted": result.batches_persisted,
            "complete": result.complete,
            "last_evaluated_key_present": bool(result.last_evaluated_key),
        }
        if result.segment_states is not None:
            summary_entry["segment_count"] = len(result.segment_states)
            summary_entry["segments_complete"] = sum(
                1 for segment in result.segment_states.values() if segment.get("complete")
            )
        summary["tables"].append(summary_entry)

    print(json.dumps(summary, indent=2, sort_keys=True))


def build_parallel_start_keys(
    checkpoint: dict[str, object] | None,
    total_segments: int,
) -> dict[int, dict[str, object] | None]:
    start_keys: dict[int, dict[str, object] | None] = {}
    if not checkpoint or not isinstance(checkpoint, dict):
        return start_keys

    segments = checkpoint.get("segments")
    if not isinstance(segments, dict):
        return start_keys

    for segment in range(total_segments):
        segment_state = segments.get(str(segment))
        if not isinstance(segment_state, dict):
            continue
        last_evaluated_key = segment_state.get("last_evaluated_key")
        if isinstance(last_evaluated_key, dict):
            start_keys[segment] = last_evaluated_key
    return start_keys


def build_parallel_segment_states(checkpoint: dict[str, object] | None) -> dict[str, dict[str, Any]]:
    if not checkpoint or not isinstance(checkpoint, dict):
        return {}
    segments = checkpoint.get("segments")
    if not isinstance(segments, dict):
        return {}
    return {
        str(segment): dict(state)
        for segment, state in segments.items()
        if isinstance(segment, str) and isinstance(state, dict)
    }


def segment_state_from_result(segment_result: SegmentSyncResult) -> dict[str, Any]:
    return {
        "last_evaluated_key": segment_result.last_evaluated_key,
        "complete": segment_result.complete,
        "pages_read": segment_result.pages_read,
        "records_persisted": segment_result.records_persisted,
        "batches_persisted": segment_result.batches_persisted,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def find_default_env_file() -> str | None:
    candidates = (
        Path.cwd() / ".env.aws",
        Path.cwd().parent / ".env.aws",
        Path.cwd().parent.parent / ".env.aws",
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


if __name__ == "__main__":
    main()
