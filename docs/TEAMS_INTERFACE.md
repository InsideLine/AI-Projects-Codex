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

Local run:

```bash
python -m pip install -e ".[api]"
python -m uvicorn license_agent.api:app --reload
```

## Production Integration

Use Microsoft Bot Framework or an Azure Bot registered for Teams. The bot should forward user messages to the agent API, then format the response as:

- Short summary in the Teams thread.
- Link to full report.
- Buttons for "finding accepted", "finding wrong", and "needs more evidence".

The API stub currently parses the subject only. Production work must add authentication, tenant validation, report retrieval from Aurora, and feedback commands.

