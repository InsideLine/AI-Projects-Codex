# AWS Deployment

## What This Deploys

The production stack in `infra/aws-system.yml` creates:

- A Lambda-backed FastAPI bot endpoint exposed by a Lambda Function URL.
- A DynamoDB table for Teams chat history, feedback memory, and report jobs.
- S3 buckets for raw landing data, generated reports, and Athena results.
- A Glue database and raw-data crawler.
- An ECS/Fargate scheduled task that runs the DynamoDB source sync weekly.
- IAM roles for the API, Glue crawler, EventBridge scheduler, and sync task.

SOLO weekly export is intentionally not active yet. The stack leaves storage and configuration room for SOLO data, but no scheduled SOLO pull is created.

## Required Deploy-Time Inputs

Set these environment variables before running `scripts/deploy_aws_system.sh`:

```bash
export ARTIFACT_BUCKET=existing-deploy-artifact-bucket
export RAW_DATA_BUCKET=globally-unique-license-agent-raw
export REPORTS_BUCKET=globally-unique-license-agent-reports
export ATHENA_RESULTS_BUCKET=globally-unique-license-agent-athena-results
export SYNC_WORKER_IMAGE_URI=123456789012.dkr.ecr.us-east-1.amazonaws.com/license-agent-sync:latest
export TEAMS_SHARED_SECRET='long-random-secret'
export SYNC_SUBNET_IDS=subnet-abc,subnet-def
export SYNC_SECURITY_GROUP_IDS=sg-abc
```

Optional:

```bash
export AURORA_DATABASE_URL='postgresql://...'
export DYNAMODB_SOURCE_ROLE_ARN='arn:aws:iam::<source-account-id>:role/<role-name>'
export DYNAMODB_SOURCE_EXTERNAL_ID='external-id-if-required'
export WEEKLY_SYNC_SCHEDULE_EXPRESSION='cron(0 7 ? * SUN *)'
```

## Weekly AWS Usage Sync

The weekly sync runs:

```bash
python -m license_agent.aws_sync_worker
```

It scans:

- `ProcessInfo`
- `SiteInfo`
- `TenantInfo`

The worker writes raw DynamoDB attribute JSON batches to S3 under:

```text
s3://<raw-bucket>/raw/aws_dynamodb_weekly/<table>/<yyyy>/<mm>/<dd>/<batch-id>/
```

For the cross-account source, prefer `DYNAMODB_SOURCE_ROLE_ARN`. The task role can assume that role and scan the source tables without storing long-lived source-account access keys.

## Sync Runtime Choice

The AWS usage sync is an ECS/Fargate scheduled task instead of Lambda because `ProcessInfo` is large. A full scan previously produced roughly 13 million local rows, which is too large for a comfortable Lambda execution window.

## Build And Push Sync Image

Create an ECR repository once, then:

```bash
export ECR_REPOSITORY_URI=123456789012.dkr.ecr.us-east-1.amazonaws.com/license-agent-sync
./scripts/build_and_push_sync_image.sh
```

Use the printed image URI as `SYNC_WORKER_IMAGE_URI`.

## Deploy Stack

The deploy script builds the Lambda package, uploads it to the artifact bucket, and deploys CloudFormation:

```bash
./scripts/deploy_aws_system.sh
```

The stack output `BotEndpointBaseUrl` is the base URL. The Teams relay should post to:

```text
<BotEndpointBaseUrl>/teams/message
```

Include this header:

```text
x-license-agent-secret: <TEAMS_SHARED_SECRET>
```

## Post-Deploy Checks

```bash
curl <BotEndpointBaseUrl>/health
curl -H "x-license-agent-secret: $TEAMS_SHARED_SECRET" <BotEndpointBaseUrl>/teams/state
```

## Remaining Production Wiring

- Register/update the Azure Bot/Teams app to call the deployed endpoint.
- Replace or augment the shared-secret gate with full Bot Framework JWT validation when the bot registration details are available.
- Configure `AURORA_DATABASE_URL` once the CRM Aurora endpoint is ready.
- Run the Glue crawler after the first raw S3 sync.
- Add curated Parquet conversion jobs before high-volume Athena usage.
- Add the SOLO scheduled export later.
