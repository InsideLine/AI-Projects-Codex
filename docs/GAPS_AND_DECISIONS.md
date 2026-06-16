# Gaps And Decisions

## Main Decisions

Use S3 plus Aurora PostgreSQL. S3 preserves source extracts and supports backfills. Aurora supports the interactive, cross-source joins needed for Teams reports and investigations.

Start with deterministic rules before adding an LLM. License violation work needs repeatability, evidence, and human review. The LLM should summarize and ask follow-up questions, not invent findings.

The agent should learn through structured feedback, not uncontrolled self-training. Analyst feedback should become labeled examples, rule changes, threshold changes, or reviewed prompt/context updates.

## Gaps To Resolve

1. SOLO access method is not specified.
   - Need to confirm API, database replica, scheduled export, or manual file drop.

2. The AWS usage source is linked as a file folder, not a system contract.
   - Need actual storage format, owner account, region, schema, and refresh cadence.

3. Zoho fields for personnel licensed and organization definition may be unstructured.
   - Need mappings from CRM records to license IDs and a way to parse or curate organization definitions.

4. "Organization definition" needs a formal interpretation.
   - Does it mean legal entity, geography, domain, subsidiary list, users, sites, or a combination?

5. The 100 GB per personnel threshold is a suspicion signal.
   - Reports must phrase this as "review threshold exceeded" rather than proof.

6. First activation to first usage pairing needs domain rules.
   - If multiple activations and multiple usage streams exist, pairing should consider dates, machine names, MAC addresses, IPs, and product version.

7. IP geolocation is inherently approximate.
   - The system should store provider, timestamp, and accuracy radius, and should flag low-confidence evidence.

8. Feedback governance is needed.
   - Decide who can mark findings wrong, who can approve rule changes, and whether feedback affects only future reports or also reopens prior reports.

## Geolocation Alternatives To WhatIsMyIPAddress

Recommended starting option: MaxMind GeoIP City database for local, repeatable batch enrichment, with IPinfo as an optional API comparison source for disputed or low-confidence cases.

Shortlist:

- MaxMind GeoIP City database: local database, no per-query latency, includes city/state/country and accuracy radius. Strong fit for scheduled batch enrichment.
- IPinfo: API and database download options, includes radius and last-changed style context. Useful as a second provider or API-first path.
- DB-IP: downloadable IP geolocation databases. Worth evaluating for cost and licensing.
- IP2Location: downloadable databases with different field bundles. Worth evaluating if you need packaged ASN/proxy add-ons.

Operational recommendation:

1. Normalize all IP lookup results into `ip_geolocations`.
2. Cache lookups permanently with provider version or lookup timestamp.
3. Use one primary provider for routine analysis.
4. Use a second provider only when location evidence is central to a high-risk report.
5. Never treat city-level geolocation as exact physical presence without corroborating evidence.

Reference pages reviewed on 2026-06-16:

- https://www.maxmind.com/en/geoip-databases
- https://ipinfo.io/data/ip-geolocation
- https://db-ip.com/db/
- https://www.ip2location.com/database/ip2location

