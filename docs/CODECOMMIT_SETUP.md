# AWS CodeCommit Setup

This workspace does not include AWS credentials, so the repository cannot be created automatically yet. Once credentials are available, run these steps from this project folder.

## Create The Repository

```bash
aws codecommit create-repository \
  --repository-name license-violation-data-analyzer-agent \
  --repository-description "Agent-assisted license violation data gathering and analysis."
```

## Initialize And Push

```bash
git init
git add .
git commit -m "Initial license violation analyzer agent scaffold"
git branch -M main
git remote add origin codecommit://license-violation-data-analyzer-agent
git push -u origin main
```

If HTTPS credentials are preferred, use the clone URL returned by `aws codecommit create-repository`.

## CI/CD Recommendation

Start with a pipeline that runs:

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

Add integration tests after Aurora and source extraction credentials are available.

