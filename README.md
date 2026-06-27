# License Violation Data Analyzer Agent

This project is an agent-assisted license verification system. It compiles LinkTek licensing, AWS usage, CRM, and IP geolocation data into auditable datasets, then produces investigation reports for a license ID or company through Microsoft Teams.

## What The System Does

The agent supports two workflows:

1. A Microsoft Teams user asks for a report by license ID or company name.
2. The bot gathers compiled AWS usage, SOLO activation/export, CRM relationship and entitlement, and IP geolocation context, then returns a Teams-friendly report plus a Word document attachment.

The agent does not query SOLO or the source AWS account on every user request. AWS ProcessInfo and SOLO exports are compiled into curated S3 summaries. CRM context is queried read-only from the existing Aurora Zoho CRM sync because that data is already relational and current.

## Current Architecture

- S3 stores raw and curated AWS usage, SOLO export, and GeoLite2 geolocation data.
- Glue/Athena remain the planned analytical query layer for raw and refined S3 datasets.
- Aurora PostgreSQL is used read-only through the RDS Data API for CRM relationship, Customer License, linked record, entitlement, and organization-scope context.
- Lambda runs the FastAPI bot through Mangum.
- Microsoft Teams posts Bot Framework activities to the Lambda Function URL.
- DynamoDB stores chat history, report jobs, and feedback memory.
- S3 stores generated Word reports with encrypted objects and presigned download links.

The high-volume usage and SOLO datasets stay in S3 because the workload does not need instant query latency. If reports later need broader SQL joins across usage data, add Athena views or a curated Aurora subset without changing the raw landing pattern.

## Project Layout

- `src/license_agent/`: agent core, data models, Teams/Bot Framework API, S3 summary readers, Aurora query templates, report builder, and feedback store.
- `infra/aws-system.yml`: AWS deployment template for Lambda, DynamoDB, S3, Glue, and optional weekly AWS sync.
- `scripts/`: deployment, DynamoDB sync, usage summary, SOLO summary, and GeoLite2 backfill utilities.
- `docs/`: implementation notes, architecture, deployment, data requirements, and integration guides.
- `tests/`: regression tests for analysis, Teams routing, report artifacts, data queries, and summary readers.

## Local Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,api]"
python -m unittest discover tests
```

The `api` extra mirrors the Lambda runtime dependencies, including FastAPI, Mangum, boto3, Bot Framework JWT validation, Aurora access, and MaxMind cache reading.

## Implemented Now

- Teams report requests by company or license, with fuzzy company matching and clarification.
- Bot Framework JWT validation for production Teams messages.
- Shared-secret test endpoints for local and controlled smoke testing.
- AWS ProcessInfo usage summaries from S3.
- SOLO activation/export summaries from S3, indexed by normalized company and license ID.
- GeoLite2 IP geolocation cache for AWS usage IPs and SOLO activation IPs.
- Aurora CRM lookups for companies, Customer Licenses, linked SRFs, License Verification records, deals, notes, QLM keys, and quote line items.
- EULA review threshold calculation using CRM Customer License entitlement fields before falling back to linked records or SOLO export quantities.
- Word report artifacts in the reports S3 bucket.
- Structured feedback capture for future rule tuning.

## Still Deferred

- Scheduled SOLO weekly report export. The storage and configuration path exists, but the pull is intentionally deferred.
- Production weekly cross-account AWS sync. The ECS/Fargate worker and EventBridge schedule exist in the template when `ENABLE_WEEKLY_SYNC=true`, but must be enabled with the source role, image, subnet, and security group settings.
- LLM prompt orchestration. Current findings are deterministic and evidence-backed; an LLM can later summarize, ask clarifying questions, or explain findings.
