# Architecture

## Data Flow

1. Source extract jobs pull or receive files from SOLO, AWS customer usage data, and Zoho CRM.
2. Raw files are written to S3 with immutable paths and checksums.
3. Normalization jobs load Aurora tables.
4. IP addresses from activations and usage are enriched through a geolocation provider.
5. The analysis engine reads normalized data and produces report rows plus findings.
6. Teams users request reports and submit feedback.
7. Feedback is stored as labels for review, test cases, and rule improvement.

## Runtime Components

- Extract workers: scheduled jobs for SOLO, AWS, Zoho, and geolocation enrichment.
- Aurora PostgreSQL: normalized operational investigation database.
- Agent API: service that accepts Teams requests and returns report summaries.
- Report writer: produces report records and optionally PDF/HTML output.
- Feedback processor: turns analyst review into labels and proposed rule changes.

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
- Encrypt S3 buckets and Aurora.
- Retain raw extracts according to legal and compliance requirements.
- Keep report access limited to authorized license verification users.

