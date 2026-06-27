# Architecture

## Data Flow

1. Source extract jobs pull or receive AWS usage, SOLO licensing, CRM, and geolocation data.
2. Raw extracts are written to S3 with stable paths and checksums.
3. Curated builders create compact S3 summaries for report-time lookup:
   - AWS ProcessInfo company usage summary.
   - SOLO activation/export company summary indexed by company and license ID.
   - GeoLite2 IP geolocation cache keyed by public IP address.
4. CRM relationship and entitlement context is queried read-only from the Aurora Zoho CRM sync through the RDS Data API.
5. Teams users request reports by company or license.
6. The report builder combines usage, SOLO, CRM, and IP context into findings, a Teams summary, and a Word document artifact.
7. Reviewer feedback is stored as labels for rule improvement and future training.

## Runtime Components

- Agent API: FastAPI running on Lambda through Mangum.
- Teams adapter: validates Bot Framework JWTs and replies through the Bot Connector API.
- Chat/job store: DynamoDB table for user messages, queued jobs, completed reports, and feedback.
- Usage summary reader: loads `curated/aws_usage/company_usage_summary.json` from S3.
- SOLO summary reader: loads `curated/solo_softwarekey/company_activation_summary.json` from S3.
- IP cache reader: loads `curated/ip_geolocation/ip_geolocation_cache.json` from S3.
- Aurora CRM reader: allowlisted RDS Data API templates for companies, Customer Licenses, SRFs, License Verification records, deals, notes, QLM keys, and quote line items.
- Report writer: writes `.docx` reports to encrypted S3 and returns presigned links.
- Optional sync worker: ECS/Fargate scheduled task for weekly cross-account DynamoDB pulls.

## Current Storage Decision

The selected data platform is `S3 + Glue Data Catalog + Athena` for high-volume usage and licensing datasets, with Aurora used only where it already exists for CRM.

This keeps recurring cost low and avoids moving large ProcessInfo history into a transactional database before the query pattern proves it needs that. If report latency or joins become painful, add Athena views first; add a curated Aurora subset only if Athena is not enough.

## Report Generation Contract

For a license ID or company, the report should include:

- Executive summary and evaluation.
- AWS usage totals, date range, machine names, MAC addresses, users, and public IPs.
- SOLO activation/export metrics, including activation counts, rejected activations, deactivations, installation IDs, and license IDs.
- IP geolocation for AWS public IPs and SOLO activation IPs, with city/region/country and accuracy radius.
- CRM relationship, ownership, purchase, entitlement, organization definition, notes, SRFs, License Verification records, deals, and quote-line context when present.
- EULA threshold calculation using the best available entitlement denominator.
- Findings and recommended human review steps.
- Word document output for copying, sharing, and archival use.

## Security Notes

- Treat IP addresses, usernames, MAC addresses, tenant IDs, database names, notes, and report artifacts as sensitive investigation data.
- Store credentials in AWS Secrets Manager.
- Use least-privilege IAM roles for cross-account AWS extraction and Aurora read access.
- Keep the Lambda Function URL internet reachable for Teams, but validate Bot Framework activities with JWTs.
- Keep non-Bot Framework test endpoints protected by `x-license-agent-secret`.
- Encrypt raw data, report artifacts, and Athena results in S3.
- Presigned report links are convenient but time-limited; shorten expiry or move to authenticated downloads if reports need tighter handling.
