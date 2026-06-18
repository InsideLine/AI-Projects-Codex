from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from license_agent.dynamodb_sync import (
    build_dynamodb_client,
    load_checkpoints,
    resolve_source_account_id,
    save_checkpoints,
    sync_dynamodb_table,
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
    client = build_dynamodb_client(region_name=settings.aws_region, profile_name=settings.aws_profile)
    source_account = resolve_source_account_id(region_name=settings.aws_region, profile_name=settings.aws_profile)

    checkpoint_path = Path(args.checkpoint_path)
    checkpoints = {} if args.reset_checkpoints else load_checkpoints(checkpoint_path)
    tables = tuple(args.tables)

    summary: dict[str, object] = {
        "source_account": source_account,
        "region": settings.aws_region,
        "landing_zone": landing_zone.health(),
        "tables": [],
    }

    for table_name in tables:
        start_key = None
        checkpoint = checkpoints.get(table_name)
        if checkpoint and isinstance(checkpoint, dict):
            start_key = checkpoint.get("last_evaluated_key")

        result = sync_dynamodb_table(
            client,
            landing_zone,
            table_name=table_name,
            source_account=source_account,
            page_limit=args.page_limit,
            max_pages=args.max_pages,
            start_key=start_key,
        )
        update_checkpoint_for_result(checkpoints, result)
        save_checkpoints(checkpoint_path, checkpoints)
        summary["tables"].append(
            {
                "table_name": result.table_name,
                "pages_read": result.pages_read,
                "records_persisted": result.records_persisted,
                "batches_persisted": result.batches_persisted,
                "complete": result.complete,
                "last_evaluated_key_present": bool(result.last_evaluated_key),
            }
        )

    print(json.dumps(summary, indent=2, sort_keys=True))


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
