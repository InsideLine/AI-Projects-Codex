# Teams App Package

This folder contains the Teams app manifest for the License Violation Data Analyzer Agent.

## Files needed for upload

Teams app packages are zip files containing these files at the zip root:

- `manifest.json`
- `color.png`
- `outline.png`

The manifest is configured with the bot app ID from AWS Secrets Manager. It intentionally uses Teams manifest schema `1.17` because that matches the known-good manifest pattern used by the existing internal bot apps.

## Azure Bot messaging endpoint

Set the Azure Bot messaging endpoint separately from this manifest:

```text
https://hebtz6gipu7gduf3luzuxlqfq40jmame.lambda-url.us-east-1.on.aws/teams/message
```

## Single-tenant note

The Teams app is configured as single tenant in Entra/Azure. The tenant ID is stored in AWS Secrets Manager under:

```text
license-violation-agent/ms-teams-app
```

The manifest references the bot app ID, but the app secret and tenant secret stay out of the Teams package.
