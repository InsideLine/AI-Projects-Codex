# Microsoft Teams Interface

## Intended Commands

```text
license LIC-12345
company Example Corp
feedback usage_over_eula_review_threshold accepted This matched the analyst review.
feedback company_name_mismatch wrong Subsidiary name is already covered by contract amendment.
```

## API Stub

The current FastAPI stub exposes:

- `GET /health`
- `POST /teams/message`
- `GET /teams/state`
- `GET /teams/jobs/{job_id}`

Local run:

```bash
python -m pip install -e ".[api]"
python -m uvicorn license_agent.api:app --reload
```

## Production Integration

Use Microsoft Bot Framework or an Azure Bot registered for Teams. Teams posts activities to:

```text
<BotEndpointBaseUrl>/teams/message
```

The deployed API validates Bot Framework bearer tokens before processing Teams activities. Non-Bot-Framework smoke-test payloads must use the `x-license-agent-secret` header.

## Recommended Runtime Pattern

Pattern this after the existing Axiom chat-bot shape rather than a fully autonomous managed agent:

1. Teams sends a message to a bot endpoint.
2. The bot calls a small API service in AWS.
3. The API classifies the intent, records chat memory, and queues a report job.
4. The report builder loads compiled S3 summaries plus Aurora CRM context, runs deterministic checks, and stores the result.
5. The Teams reply includes a copy-friendly Markdown report and, when S3 reports are configured, a Word document link.
6. An LLM can later summarize, ask clarification questions, or explain findings, but the findings themselves stay evidence-based.
7. Reviewer feedback is stored as structured labels so the system learns which signals are useful.

This does not need to be "agentic" in the Bedrock Agents sense for v1. A Bedrock Converse style assistant with explicit tool calls, memory, and queued jobs is the better fit here.

The local scaffold now supports:

- queued report requests by license or company
- lightweight user memory of common requests
- job status polling
- structured feedback capture
- constrained data questions against the latest SOLO analysis report
- read-only CRM company lookup through Aurora
- read-only active LinkTek license lookup through Aurora
- read-only lookup of CRM records linked to those active LinkTek licenses
- investigation-report fallback to historical LinkTek Customer License rows when AWS usage references an expired license

Production work still must add richer adaptive-card actions for feedback. Authentication, Teams/Bot Framework plumbing, Aurora CRM lookup, S3 usage summaries, and Word report artifacts are already implemented.

## Data Questions

The chatbot can now route natural-language data questions separately from report-generation requests. Examples:

```text
What are the strongest violation signals?
How many licenses are in the current SoftwareKey dataset?
Is Hudson Housing Capital LLC in the violator overlap?
Look up Hudson Housing Capital LLC in CRM.
Show active LinkTek licenses for Hudson Housing Capital LLC.
Show records linked to license id LTK-1234.
Show linked records for active LinkTek licenses for Hudson Housing Capital LLC.
```

CRM lookup is intentionally constrained to allowlisted Aurora query templates. Ad hoc active-license questions filter to LinkTek licenses that are marked active and have an expiry date after today. Investigation reports first try active rows, then fall back to historical LinkTek Customer License rows by observed AWS usage license ID so older ProcessInfo evidence can still be reviewed. Configure:

```text
AURORA_DATABASE_URL=
AURORA_CRM_SCHEMA=public
AURORA_CRM_ACCOUNTS_TABLE=accounts
AURORA_CRM_COMPANY_NAME_COLUMN=company_name
AURORA_CRM_LICENSES_TABLE=customer_licenses
AURORA_CRM_LICENSE_ID_COLUMN=id
AURORA_CRM_LICENSE_CODE_COLUMN=license_code
AURORA_CRM_LICENSE_COMPANY_COLUMN=company
AURORA_CRM_LICENSE_ENTITY_COLUMN=entity
AURORA_CRM_LICENSE_ACTIVE_COLUMN=active_license
AURORA_CRM_LICENSE_EXPIRY_COLUMN=maintenance_expiry_date
AURORA_CRM_LINKTEK_ENTITY_VALUE=LinkTek
AURORA_CRM_SRF_TABLE=sales_routing_forms
AURORA_CRM_SRF_LICENSE_COLUMN=license_id
AURORA_CRM_LICENSE_VERIFICATIONS_TABLE=license_verifications
AURORA_CRM_LICENSE_VERIFICATIONS_LICENSE_COLUMN=existing_license_record
AURORA_CRM_QUOTE_LINE_ITEMS_TABLE=quote_line_item_sets
AURORA_CRM_QUOTE_LINE_ITEMS_LICENSE_COLUMN=customer_license_record
```

The bot does not run arbitrary SQL from chat. Natural language is mapped onto explicit read-only query templates.
