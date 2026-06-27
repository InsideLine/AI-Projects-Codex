# IP Geolocation Backfill

This project uses MaxMind GeoLite2 City for offline IP geolocation backfills.

## Why Offline

The bot should not call an external IP API during every report. Instead, we enrich unique IP addresses in batches and store the result as a cache. Reports read the cache by IP address.

## Required Input

Download `GeoLite2-City.mmdb` from MaxMind using a MaxMind account and license key. MaxMind requires account credentials for GeoLite2 downloads.

Recommended local path:

```bash
local_data/reference/geolite2/GeoLite2-City.mmdb
```

Do not commit the `.mmdb` file to git.

## Backfill Command

```bash
PYTHONPATH=src python3 scripts/backfill_geolite2_ip_cache.py \
  --database local_data/reference/geolite2/GeoLite2-City.mmdb
```

Default outputs:

```text
local_data/curated/ip_geolocation/ip_geolocation_cache.json
local_data/curated/ip_geolocation/ip_geolocation_cache.csv
```

The script currently collects unique public IPs from:

- `local_data/curated/aws_usage/company_usage_summary.json`
- `local_data/raw/solo_softwarekey/activation_data/**/*.csv`

## Publish To S3

```bash
aws s3 cp \
  local_data/curated/ip_geolocation/ip_geolocation_cache.json \
  s3://license-violation-agent-raw-888442823671-us-east-1/curated/ip_geolocation/ip_geolocation_cache.json

aws s3 cp \
  local_data/curated/ip_geolocation/ip_geolocation_cache.csv \
  s3://license-violation-agent-raw-888442823671-us-east-1/curated/ip_geolocation/ip_geolocation_cache.csv
```

The deployed bot reads the JSON cache by default from:

```text
curated/ip_geolocation/ip_geolocation_cache.json
```

## Report Caveat

GeoLite2 City is approximate. Reports should show city, region, country, and accuracy radius, and should not treat the result as a street address, building location, or exact office location.

## Future Active-License Filter

Once Aurora CRM active-license access is configured, the backfill can be narrowed to IPs associated with active LinkTek licenses only. Until then, this command backfills all unique public IPs present in the local AWS usage summary and SOLO activation exports.
