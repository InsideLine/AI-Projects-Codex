# DynamoDB Sync

## Current Source

We now have verified read access to three DynamoDB tables in the source AWS account:

- `ProcessInfo`
- `SiteInfo`
- `TenantInfo`

The sync job is designed to pull those tables in pages, persist each page as a raw batch, and save a resume checkpoint after every table run. For large tables, it now also supports DynamoDB parallel scan segments so we can backfill faster.

## How It Works

Script:

`scripts/sync_dynamodb_tables.py`

Behavior:

1. Loads AWS credentials from an env file if provided.
2. Connects to DynamoDB in `us-east-1`.
3. Resolves the source account ID from STS.
4. Scans each configured table in pages.
5. Optionally splits a large table into parallel scan segments.
6. Stores each page under the existing raw landing zone.
7. Writes a JSON checkpoint so the next run resumes where the prior run stopped.

## Default Tables

- `ProcessInfo`
- `SiteInfo`
- `TenantInfo`

## Checkpoints

Default checkpoint path:

`local_data/checkpoints/dynamodb_sync.json`

This file stores `LastEvaluatedKey` per table for single-threaded runs, or per segment for parallel scan runs, so large scans can be resumed incrementally.

## Recommended First Runs

Small smoke test:

```bash
PYTHONPATH=src python3 scripts/sync_dynamodb_tables.py --page-limit 25 --max-pages 1
```

Larger incremental sync:

```bash
PYTHONPATH=src python3 scripts/sync_dynamodb_tables.py --page-limit 250 --max-pages 20
```

Faster full backfill for the large table using parallel scan:

```bash
PYTHONPATH=src python3 scripts/sync_dynamodb_tables.py \
  --table ProcessInfo \
  --parallel-segments 8 \
  --page-limit 1000 \
  --checkpoint-path local_data/checkpoints/dynamodb_sync_parallel.json \
  --source-system aws_dynamodb_full
```

Fresh restart from page one:

```bash
PYTHONPATH=src python3 scripts/sync_dynamodb_tables.py --page-limit 250 --max-pages 1 --reset-checkpoints
```

## Why Incremental Pages Matter

`ProcessInfo` is large, so a full-table pull should not be treated casually. The current design supports repeated bounded runs, plus segmented parallel backfills, so we can steadily move the table into our landing zone without hammering a single long-running scan path.

## Next Likely Upgrade

After the raw landing flow is proven, the next step is:

1. write landed raw batches to S3 instead of only local disk
2. add Glue catalog registration
3. optionally convert raw JSON batches to partitioned Parquet for Athena efficiency
