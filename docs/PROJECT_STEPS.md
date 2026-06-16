# Project Steps

## Phase 1: Discovery And Data Contracts

1. Confirm source ownership, access paths, credentials, and rate limits for SOLO, the other AWS account, and Zoho CRM.
2. Export representative samples from each source.
3. Validate the raw checklist fields against real column names and edge cases.
4. Define canonical IDs for company, license, tenant, machine, user, MAC address, and IP address.
5. Decide which fields contain sensitive data and set retention rules.

## Phase 2: Data Platform

1. Create S3 buckets for raw extracts, normalized snapshots, and generated reports.
2. Create Aurora PostgreSQL using `infra/schema.sql`.
3. Build extract jobs:
   - SOLO activation export.
   - Cross-account AWS customer usage export.
   - Zoho CRM account/license entitlement export.
   - IP geolocation enrichment job.
4. Store extract metadata and checksums for auditability.
5. Reconcile row counts between raw files and normalized tables.

## Phase 3: Analysis Agent

1. Implement deterministic checklist rules first.
2. Add report generation for license ID and company name.
3. Add analyst feedback capture for accepted/incorrect findings.
4. Add a reviewed rule-update process that converts feedback into changed thresholds, new consistency checks, or model examples.
5. Add LLM summarization only after the factual findings are stable and test-covered.

## Phase 4: Microsoft Teams Interface

1. Register an Azure Bot or Teams app.
2. Route Teams messages to the API endpoint.
3. Implement commands:
   - `license LIC-123`
   - `company Example Corp`
   - `feedback <finding> accepted|wrong <comment>`
4. Return a short Teams summary and attach or link the full report.

## Phase 5: Testing And Deployment

1. Unit-test rule behavior with known positive and negative examples.
2. Contract-test all source extract parsers.
3. Integration-test Aurora writes and report retrieval.
4. Regression-test accepted analyst feedback examples.
5. Deploy through CodeCommit and CI/CD after credentials are confirmed.

