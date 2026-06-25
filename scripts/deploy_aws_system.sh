#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_NAME="${STACK_NAME:-license-violation-agent}"
AWS_REGION="${AWS_REGION:-us-east-1}"
PROJECT_NAME="${PROJECT_NAME:-license-violation-agent}"
ARTIFACT_BUCKET="${ARTIFACT_BUCKET:?Set ARTIFACT_BUCKET to an existing deployment artifact bucket.}"
RAW_DATA_BUCKET="${RAW_DATA_BUCKET:?Set RAW_DATA_BUCKET to a globally unique raw data bucket name.}"
REPORTS_BUCKET="${REPORTS_BUCKET:?Set REPORTS_BUCKET to a globally unique reports bucket name.}"
ATHENA_RESULTS_BUCKET="${ATHENA_RESULTS_BUCKET:?Set ATHENA_RESULTS_BUCKET to a globally unique Athena results bucket name.}"
SYNC_WORKER_IMAGE_URI="${SYNC_WORKER_IMAGE_URI:-not-configured}"
ENABLE_WEEKLY_SYNC="${ENABLE_WEEKLY_SYNC:-false}"
ENABLE_GLUE_CATALOG="${ENABLE_GLUE_CATALOG:-false}"
TEAMS_SHARED_SECRET="${TEAMS_SHARED_SECRET:?Set TEAMS_SHARED_SECRET for bot endpoint protection.}"
TEAMS_APP_SECRET_NAME="${TEAMS_APP_SECRET_NAME:-license-violation-agent/ms-teams-app}"
SYNC_SUBNET_IDS="${SYNC_SUBNET_IDS:-}"
SYNC_SECURITY_GROUP_IDS="${SYNC_SECURITY_GROUP_IDS:-}"
AURORA_DATABASE_URL="${AURORA_DATABASE_URL:-}"
DYNAMODB_SOURCE_ROLE_ARN="${DYNAMODB_SOURCE_ROLE_ARN:-}"
DYNAMODB_SOURCE_EXTERNAL_ID="${DYNAMODB_SOURCE_EXTERNAL_ID:-}"
WEEKLY_SYNC_SCHEDULE_EXPRESSION="${WEEKLY_SYNC_SCHEDULE_EXPRESSION:-cron(0 7 ? * SUN *)}"

ZIP_PATH="$("${ROOT_DIR}/scripts/build_lambda_package.sh")"
LAMBDA_SHA="$(shasum -a 256 "${ZIP_PATH}" | awk '{print $1}')"
LAMBDA_KEY="license-agent/api/${LAMBDA_SHA}/$(basename "${ZIP_PATH}")"

python3 -m awscli s3 cp "${ZIP_PATH}" "s3://${ARTIFACT_BUCKET}/${LAMBDA_KEY}" --region "${AWS_REGION}"

python3 -m awscli cloudformation deploy \
  --region "${AWS_REGION}" \
  --stack-name "${STACK_NAME}" \
  --template-file "${ROOT_DIR}/infra/aws-system.yml" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    ProjectName="${PROJECT_NAME}" \
    RawDataBucketName="${RAW_DATA_BUCKET}" \
    ReportsBucketName="${REPORTS_BUCKET}" \
    AthenaResultsBucketName="${ATHENA_RESULTS_BUCKET}" \
    LambdaPackageBucket="${ARTIFACT_BUCKET}" \
    LambdaPackageKey="${LAMBDA_KEY}" \
    SyncWorkerImageUri="${SYNC_WORKER_IMAGE_URI}" \
    EnableWeeklySync="${ENABLE_WEEKLY_SYNC}" \
    EnableGlueCatalog="${ENABLE_GLUE_CATALOG}" \
    TeamsSharedSecret="${TEAMS_SHARED_SECRET}" \
    TeamsAppSecretName="${TEAMS_APP_SECRET_NAME}" \
    AuroraDatabaseUrl="${AURORA_DATABASE_URL}" \
    DynamoDBSourceRoleArn="${DYNAMODB_SOURCE_ROLE_ARN}" \
    DynamoDBSourceExternalId="${DYNAMODB_SOURCE_EXTERNAL_ID}" \
    WeeklySyncScheduleExpression="${WEEKLY_SYNC_SCHEDULE_EXPRESSION}" \
    SyncSubnetIds="${SYNC_SUBNET_IDS}" \
    SyncSecurityGroupIds="${SYNC_SECURITY_GROUP_IDS}"

python3 -m awscli cloudformation describe-stacks \
  --region "${AWS_REGION}" \
  --stack-name "${STACK_NAME}" \
  --query 'Stacks[0].Outputs' \
  --output table
