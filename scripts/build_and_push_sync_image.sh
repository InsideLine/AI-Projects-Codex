#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AWS_REGION="${AWS_REGION:-us-east-1}"
ECR_REPOSITORY_URI="${ECR_REPOSITORY_URI:?Set ECR_REPOSITORY_URI, for example 123456789012.dkr.ecr.us-east-1.amazonaws.com/license-agent-sync.}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

python3 -m awscli ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "$(echo "${ECR_REPOSITORY_URI}" | cut -d/ -f1)"

docker build -f "${ROOT_DIR}/Dockerfile.sync" -t "${ECR_REPOSITORY_URI}:${IMAGE_TAG}" "${ROOT_DIR}"
docker push "${ECR_REPOSITORY_URI}:${IMAGE_TAG}"

echo "${ECR_REPOSITORY_URI}:${IMAGE_TAG}"
