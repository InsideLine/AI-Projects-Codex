# License Violation Data Analyzer Agent

This project is the initial scaffold for an agent-assisted license verification system. It ingests licensing, product usage, CRM, and geolocation data into an auditable warehouse, then produces investigation reports for a license ID or company.

## What The System Does

The agent supports two workflows:

1. A Microsoft Teams user asks for a report by license ID or company name.
2. A scheduled or analyst-triggered investigation runs against normalized data and produces findings about possible license agreement violations.

The agent does not query SOLO, AWS usage stores, and Zoho CRM every time a user asks a question. Source data is extracted on a schedule into raw storage, normalized into warehouse tables, and analyzed from those compiled tables.

## Recommended Architecture

Use `S3 + Glue Data Catalog + Athena` first, and add Aurora PostgreSQL only if the query pattern later justifies it:

- S3 stores raw extracts exactly as received from SOLO, the other AWS account, Zoho CRM, and geolocation enrichment jobs.
- AWS Glue Data Catalog stores table metadata for landed datasets.
- Athena queries raw and refined S3 datasets with standard SQL.
- Aurora PostgreSQL remains an optional later-stage store for hot normalized data and low-latency report workflows.

This project is starting with the cheaper, slower, more flexible path because the workload is queryable but not urgent. If the report experience later needs faster relational joins, Aurora can be added for the curated subset without changing the raw landing pattern.

## Project Layout

- `src/license_agent/`: agent core, rule engine, data models, feedback store, connectors, and Teams API stub.
- `src/license_agent/`: also includes AWS CLI detection, settings loading, SOLO helpers, and Zoho CRM connection helpers.
- `src/license_agent/`: also includes raw batch ingestion and landing-zone storage helpers.
- `infra/schema.sql`: Aurora PostgreSQL schema for normalized data and investigation outputs.
- `docs/`: implementation plan, architecture, data requirements, known gaps, and CodeCommit setup.
- `context/`: compact context files intended for future AI coding tools.
- `tests/`: regression tests for the investigation engine.

## Local Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,api]"
python -m pytest
```

The core tests use the Python standard library plus pytest. The Teams API stub requires the `api` extra.

## First Implementation Status

Included now:

- Normalized data model for activations, usage, entitlements, organizations, geolocation, findings, reports, and user feedback.
- Rule engine covering the checklist: activation to usage timeline, location authorization, suspicious data volume per licensed personnel, company name mismatches, missing usage data, and incomplete IP geolocation.
- Feedback capture model so analyst corrections can become training labels and rule-tuning input.
- Pluggable geolocation interface with offline/manual, MaxMind DB, and IPinfo API provider shapes.
- CSV connector scaffolds for SOLO, AWS customer usage exports, and Zoho CRM exports.
- FastAPI Teams-facing endpoint stub.
- AWS CLI detection plus Secrets Manager-backed Zoho credential loading.
- SOLO XML service and report-export scaffolding with a bulk-first integration strategy.
- Zoho CRM setup helpers and health endpoints.
- Raw ingestion endpoint and landing-zone storage for incoming AWS table/API data.
- Tests for high-signal violation scenarios and negative cases.

Not included yet:

- Real credentials or API clients for SOLO, Zoho, AWS cross-account extraction, Microsoft Teams Bot Framework, or CodeCommit.
- Production deployment infrastructure.
- LLM prompt orchestration. The deterministic analysis core is built first so future model behavior can be tested against stable facts.
