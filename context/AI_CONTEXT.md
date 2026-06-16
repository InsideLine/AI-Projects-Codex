# AI Context

You are working on the License Violation Data Analyzer Agent.

Core goal: produce auditable license violation investigation reports from compiled SOLO activation data, AWS customer usage data, Zoho CRM entitlement/organization data, and IP geolocation enrichment.

Important principles:

- Do not query source systems on every Teams request. Use scheduled extracts and normalized warehouse tables.
- Keep raw source extracts in S3 and report/query data in Aurora PostgreSQL.
- Treat deterministic rule findings as the factual base. LLMs may summarize, ask clarifying questions, and help draft reports, but factual conclusions must cite evidence.
- Analyst feedback should be structured and stored. It can update examples, tests, thresholds, and reviewed rules.
- The 100 GB per personnel value is only a review threshold, not proof of violation.
- IP geolocation is approximate. Store provider, lookup timestamp, and accuracy radius.

Current code:

- `license_agent.models`: dataclasses for source facts, findings, reports, and feedback.
- `license_agent.analysis`: checklist-aligned rule engine.
- `license_agent.agent`: report generation service.
- `license_agent.connectors`: CSV starter connectors for SOLO, AWS usage, and Zoho.
- `license_agent.geolocation`: provider abstraction and starter providers.
- `license_agent.feedback`: local JSONL feedback store.
- `license_agent.api`: FastAPI Teams message stub.

When extending this project, add tests before changing investigation behavior.

