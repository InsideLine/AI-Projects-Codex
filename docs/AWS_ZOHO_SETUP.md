# AWS And Zoho Setup

## What We Confirmed Locally

- AWS CLI is already installed on this Mac at `/Users/joeyrogers/Library/Python/3.9/bin/aws`.
- The current shell does not have that path on `PATH`, which is why `aws --version` failed earlier.
- Claude memory confirmed the target AWS account metadata:
  - Account: `888442823671`
  - Region: `us-east-1`
- The imported Claude context did not reveal a usable AWS access key pair or an active Zoho refresh token in this workspace.

## Recommended Credential Pattern

Use one AWS credential on the machine and keep Zoho secrets in AWS Secrets Manager.

Suggested secret name:

`AxiomProjects/ZohoCRM`

Suggested JSON payload:

```json
{
  "ZOHO_DATACENTER": "com",
  "ZOHO_CLIENT_ID": "your-client-id",
  "ZOHO_CLIENT_SECRET": "your-client-secret",
  "ZOHO_REFRESH_TOKEN": "your-refresh-token",
  "ZOHO_REDIRECT_URI": "http://localhost:8000/zoho/oauth/callback",
  "ZOHO_SCOPES": "ZohoCRM.modules.ALL",
  "ZOHO_ANALYTICS_WORKSPACE_NAME": "Statistics Pilot",
  "ZOHO_ANALYTICS_WORKSPACE_ID": "1738519000007201583",
  "ZOHO_ANALYTICS_ORG_ID": "669921235"
}
```

## Local Setup

1. Copy `.env.example` to `.env`.
2. Keep `AWS_CLI_PATH=/Users/joeyrogers/Library/Python/3.9/bin/aws` unless AWS CLI moves elsewhere.
3. Configure either:
   - `ZOHO_CREDENTIALS_SECRET_NAME=AxiomProjects/ZohoCRM`
   - or direct `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, and `ZOHO_REFRESH_TOKEN`

## Verification Commands

These do not print secrets:

```bash
PYTHONPATH=src python3 scripts/check_setup.py
```

If API dependencies are installed:

```bash
python -m uvicorn license_agent.api:app --reload
```

Then check:

- `GET /aws/health`
- `GET /zoho/health`
- `GET /zoho/oauth/url`

## What Still Needs A Real Credential

To actually connect:

- an AWS access key pair or SSO profile for the target account
- a Zoho refresh token, or a Zoho grant token plus client credentials for first-time exchange

Without those values, the project can validate structure and readiness but cannot perform live AWS or Zoho calls.

