#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_NAME="${1:-license-violation-data-analyzer-agent}"
DESCRIPTION="Agent-assisted license violation data gathering and analysis."

aws codecommit create-repository \
  --repository-name "$REPOSITORY_NAME" \
  --repository-description "$DESCRIPTION"

git init
git add .
git commit -m "Initial license violation analyzer agent scaffold"
git branch -M main
git remote add origin "codecommit://$REPOSITORY_NAME"
git push -u origin main

