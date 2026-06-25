# Correlation Analysis Offload Brief

## Objective

Offload the heavy exploratory analysis that looks for correlations between known license violations and signals found in:

- SoftwareKey/SOLO license and activation data.
- LinkTek AWS usage data from DynamoDB exports.
- CRM-derived violation records and notes from the Zoho CRM backup at `/Users/joeyrogers/Documents/Axiom/Axiom Projects/Zoho Working File (Most Recent Data)`.

The Zoho CRM backup should be treated as an external source for the analysis worker. It is intentionally not bundled with the local project data export.

## Working Question

For companies or licenses with prior known violations, identify whether their pre-violation data shows measurable outpoints compared with the general active-license population.

Core hypotheses to test:

- Licenses with violations have higher activation rejection rates.
- Licenses with violations are more likely to activate from multiple IP addresses, cities, countries, machine names, MAC addresses, or installation IDs.
- Usage activity may show mismatches between company names, tenant/site names, users, databases, machines, or license records.
- Usage volume may be unusually high relative to licensed personnel counts or organization definitions.
- CRM notes and violation fields may explain which signals were meaningful versus false positives.

## Available Local Project Data

The project-local data package should include:

- `local_data/raw/solo_softwarekey`: SoftwareKey/SOLO export licenses and activation data.
- `local_data/raw/aws_dynamodb_full`: full local DynamoDB export of `ProcessInfo`, `SiteInfo`, and `TenantInfo`.
- `local_data/raw/aws_dynamodb`: earlier/smaller DynamoDB export set.
- `local_data/raw/zoho_analytics`: CRM-related extracts already pulled into this project, including Sales Routing violation and License Verification datasets.
- `local_data/analysis`: existing correlation and SOLO signal analysis outputs.
- `local_data/checkpoints`: DynamoDB sync checkpoints.

Current approximate local data sizes:

- Full AWS DynamoDB export: 8.2 GB.
- Earlier AWS DynamoDB export: 188 MB.
- Zoho Analytics extracts: 207 MB.
- SoftwareKey/SOLO exports: 10 MB.
- Analysis outputs and checkpoints: under 1 MB.

## Data Not Included

Do not include the full Zoho CRM backup folder:

`/Users/joeyrogers/Documents/Axiom/Axiom Projects/Zoho Working File (Most Recent Data)`

That folder can be mounted or read separately by the analysis worker when needed.

## Suggested Analysis Workflow

1. Normalize identities.
   - Create canonical keys for company names, license IDs, license codes, CRM account IDs, tenant/site names, and customer/license record IDs.
   - Preserve original values for auditability.

2. Build known-violation cohorts.
   - Use Sales Routing Form records with `License Violation` or `Unresolved License Violation`.
   - Use License Verification module records as a narrower secondary cohort.
   - Limit feature windows to data before the violation was caught when dates are available.

3. Build comparison cohorts.
   - Active LinkTek licenses only.
   - Exclude already-known violators from the baseline population.
   - Segment by product, license size, age, and customer type where possible.

4. Generate feature tables.
   - SOLO features: activation count, rejected activation count/rate, unique IP count, unique installation count, deactivation count, first/last activation dates, product/version spread.
   - AWS usage features: unique machine names, MAC addresses, usernames, tenant names, site names, database names, process names, time spans, total links/files/bytes processed.
   - CRM features: licensed personnel count, organization definition, licensed sites, support/sales notes, SRF violation flags, License Verification stages and findings.

5. Compare violators against baseline.
   - Rank features by effect size and practical usefulness.
   - Track precision/recall where labels are reliable enough.
   - Separate true investigative signals from fields that merely reflect customer size or usage volume.

6. Produce outputs for the chatbot.
   - A company/license-level evidence bundle.
   - A short natural-language report.
   - A structured list of signals with confidence, source rows, and reviewer feedback fields.

## Recommended Guardrails

- Keep raw evidence row references for every claim.
- Avoid treating the 100 GB per licensed personnel heuristic as proof; use it as a review threshold.
- Avoid training directly on post-violation notes or outcomes when evaluating pre-violation prediction quality.
- Keep arbitrary SQL out of the Teams bot. Map chat requests to explicit read-only query templates.
- Keep reviewer feedback structured so future model or rules changes can distinguish accepted findings from false positives.

## Next Implementation Step

Create an offline correlation worker that reads the local package plus the external CRM backup folder, writes normalized feature tables, and produces a repeatable report under `local_data/analysis/offloaded_correlation/<timestamp>/`.
