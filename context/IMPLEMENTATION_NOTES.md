# Implementation Notes

## Near-Term Backlog

1. Add repository layer for Aurora reads and writes.
2. Add real source extract jobs or file-drop ingestion.
3. Add report persistence and report rendering.
4. Add Teams auth and Bot Framework integration.
5. Add feedback commands and reviewer roles.
6. Add rule versioning so old reports can be reproduced.
7. Build sample fixtures from sanitized real exports.

## Suggested Rule Additions

- Same license used in mutually exclusive geographies within impossible travel windows.
- Usage by machine names or tenants unrelated to the licensed company.
- New tenant IDs after deactivation date.
- Usage before license entered date.
- Duplicate MAC addresses across unrelated companies.
- Sudden usage spikes after long inactivity.
- Company aliases or subsidiaries covered by contract amendments.

## Testing Strategy

- Unit tests for every rule code.
- Golden report tests for known historical investigations.
- Parser tests for each source format.
- Migration tests for `infra/schema.sql`.
- Feedback regression tests: any finding marked wrong by an analyst should become a fixture.

