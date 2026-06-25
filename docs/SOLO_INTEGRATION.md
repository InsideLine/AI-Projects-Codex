# SOLO Integration

## Current Conclusion

Use SOLO programmatic report exports for bulk investigation datasets, not per-license XML web service calls.

Why:

- The XML License Service is oriented around single-license operations such as `Add`, `GetLicenseCustomData`, `UpdateLicenseCustomData`, `InfoCheck`, and related update methods.
- The official report integration docs state that many SOLO reports can be accessed programmatically through HTTP GET or POST and returned as `Csv`, `Xls`, `XmlElements`, or `XmlParameters`.
- For the license violation project, this is the safer starting point because it avoids one-request-per-license fanout.

## Mapping The Existing Deluge Code

The existing Deluge snippet is using:

- endpoint: `XmlLicenseService.asmx/AddS`
- content type: `application/x-www-form-urlencoded`
- body field: `xml=<LicenseAdd ...>`

That maps directly to Python in [solo.py](/Users/joeyrogers/Documents/Axiom/Axiom%20Projects/Codex/License%20Violation%20Data%20Analyzer%20Agent/src/license_agent/solo.py), which builds the same XML payload shape and posts it to the same service family.

## Recommended Extraction Strategy

1. Start with programmatic report exports for bulk acquisition.
2. Use XML License Service only for narrow record-level lookups or write operations.
3. Use `Export Licenses` as the main incremental sync feed and `Export Activation Data` as the activation-history feed.
4. Prefer incremental weekly pulls for licenses using modified-date filtering when available.
5. Cache raw SOLO exports into S3, then normalize into Athena-curated datasets and later Aurora only if needed.
6. Avoid calling SOLO live from user-facing Teams requests.

## Why Reports First

The docs confirm:

- report access is available programmatically for many report families
- requests are authenticated with `WebServiceLogin=True`, `AuthorID`, `UserID`, and `UserPassword`
- output can be `Csv`, `Xls`, `XmlElements`, or `XmlParameters`

That makes reports the best fit for scheduled batch ingestion.

SoftwareKey support also confirmed that report pulls are metered as API/report activity rather than customer activation credits, and that a weekly bulk export pattern is the intended use case for this integration.

## Confirmed Report Pair

The current best-fit report pair is:

- `Export Licenses`: main license/customer snapshot and incremental weekly sync source
- `Export Activation Data`: activation-specific history, limited to one year per export window

Recommended sync pattern:

1. Pull `Export Licenses` weekly with a modified-date watermark.
2. Pull `Export Activation Data` in rolling one-year windows for backfills, then on a regular cadence for current data.
3. Land every raw export in dated S3 prefixes and preserve the original files.

## Important Gap

The docs do not, by themselves, tell us which exact SOLO report contains every field we need for investigations. They explicitly say report-specific parameters are discovered from the report form itself in the SOLO author UI.

So the next SOLO-specific task is:

1. Log into the SOLO author UI.
2. Identify the report or reports that expose:
   - Activation Date
   - License ID
   - Company Name
   - License Entered Date
   - Status
   - IP Address
   - Initial Product Version
   - Deactivated Date
3. Inspect the report form field names and filters.
4. Encode those parameters in the Python batch exporter.

## Credentials

Use an Integration User, not a regular author login.

Suggested secret name:

`AxiomProjects/SOLO`

Suggested JSON payload:

```json
{
  "SOLO_BASE_URL": "https://secure.softwarekey.com/solo",
  "SOLO_AUTHOR_ID": "your-author-id",
  "SOLO_API_USER_ID": "your-api-user-id",
  "SOLO_API_USER_PASSWORD": "your-api-user-password"
}
```

## Health Check

`GET /solo/health`

This only verifies local configuration readiness. It does not make a live SOLO call.
