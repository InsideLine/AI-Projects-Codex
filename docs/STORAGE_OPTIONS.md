# Storage Options

## Recommendation

Start with `S3 + Glue Data Catalog + Athena` as the primary data store for incoming source batches.

Why this is the best fit for the current need:

- Amazon Athena analyzes data directly in Amazon S3 using standard SQL and is serverless, so there is no infrastructure to manage and you pay only for the queries you run. This matches a workload that is queryable but not urgent. [Athena User Guide](https://docs.aws.amazon.com/athena/latest/ug/what-is.html)
- AWS Glue Data Catalog is a persistent metadata store for tables and can be used by Athena, with crawlers available to infer schema from landed files. [Glue User Guide](https://docs.aws.amazon.com/glue/latest/dg/components-overview.html)
- Athena requires an S3 output location for query results, which fits naturally with an S3 landing zone design. [Athena User Guide](https://docs.aws.amazon.com/athena/latest/ug/creating-databases-prerequisites.html)

## Best Current Path

1. Land raw payloads in S3.
2. Convert high-volume JSON to partitioned Parquet on a schedule.
3. Catalog tables in Glue.
4. Query with Athena.
5. Promote only the hot, normalized subset into Aurora later if human-facing workflows need faster joins.

## Options

### Option 1: S3 + Athena + Glue

Best for:

- scheduled ingestion
- non-urgent analytical queries
- cost efficiency
- keeping raw source history forever

Tradeoffs:

- slower than an always-on relational database for repeated joins
- better suited to batch reads than transactional updates

### Option 2: Aurora PostgreSQL

Best for:

- frequent relational joins
- low-latency application reads
- report generation that needs normalized, indexed tables

Tradeoffs:

- a continuously managed relational database is heavier than Athena for occasional queries
- you own more schema tuning and query tuning work

AWS notes that Aurora is a fully managed relational engine compatible with PostgreSQL and MySQL, with automatic storage growth and clustering features. [Aurora User Guide](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/CHAP_AuroraOverview.html)

### Option 3: Hybrid

This is the long-term architecture I would choose for this project:

- S3 is the raw source of truth.
- Athena is the first query surface for broad investigations and ad hoc research.
- Aurora stores the normalized entities and findings that power the agent’s human-facing report experience.

## What I Implemented

The codebase now has a generic receiving endpoint that stores raw batches in a filesystem landing zone using the same shape we would use in S3:

- endpoint: `POST /ingest/raw-batch`
- readiness check: `GET /ingest/health`

Local landing path pattern:

`local_data/raw/<source_system>/<dataset>/YYYY/MM/DD/<batch_id>/`

Each batch writes:

- `records.jsonl`
- `manifest.json`

This gives us an immediate receiving end today and a low-friction path to an S3 bucket tomorrow.

