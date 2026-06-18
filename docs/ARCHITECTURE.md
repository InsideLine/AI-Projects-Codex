# Architecture

## Data Flow

1. Source extract jobs pull or receive files from SOLO, AWS customer usage data, and Zoho CRM.
2. Raw files are written to S3 with immutable paths and checksums.
3. AWS Glue catalogs the raw and refined datasets.
4. Athena queries the raw and refined datasets for scheduled analysis and ad hoc investigation.
5. Normalization jobs can later load a curated subset into Aurora if lower-latency relational access becomes necessary.
6. IP addresses from activations and usage are enriched through a geolocation provider.
7. The analysis engine reads compiled datasets and produces report rows plus findings.
8. Teams users request reports and submit feedback.
9. Feedback is stored as labels for review, test cases, and rule improvement.

## Runtime Components

- Extract workers: scheduled jobs for SOLO, AWS, Zoho, and geolocation enrichment.
- S3 landing zone: raw source-of-truth storage for incoming batches and exports.
- AWS Glue Data Catalog: table metadata and crawler-managed discovery.
- Athena: primary query surface for non-urgent analytical investigation work.
- Aurora PostgreSQL: optional later-stage store for curated hot data if query latency becomes a product requirement.
- Agent API: service that accepts Teams requests and returns report summaries.
- Report writer: produces report records and optionally PDF/HTML output.
- Feedback processor: turns analyst review into labels and proposed rule changes.

## Current Decision

The selected default architecture is `S3 + Glue Data Catalog + Athena`.

Aurora is intentionally deferred until we have evidence that:

- Athena query latency is slowing investigator workflows, or
- the report experience needs indexed relational joins often enough to justify a continuously managed database

## Report Generation Contract

For a license ID or company, the report should include:

- Subject, generation time, data freshness, and source extracts used.
- Activation timeline.
- Usage timeline.
- IP locations with provider, lookup timestamp, and accuracy radius.
- Location comparison against organization definition.
- Activation to usage pairings.
- Data processed totals.
- GB per licensed personnel.
- Illogical or inconsistent fields.
- Evaluation and evidence-backed findings.
- Human review status and feedback history.

## Security Notes

- Treat IP addresses, usernames, MAC addresses, tenant IDs, and database names as sensitive investigation data.
- Use least-privilege IAM roles for cross-account AWS extraction.
- Store source credentials in AWS Secrets Manager.
- Encrypt S3 buckets, Athena query-results buckets, Glue-connected assets, and Aurora if Aurora is added later.
- Retain raw extracts according to legal and compliance requirements.
- Keep report access limited to authorized license verification users.
