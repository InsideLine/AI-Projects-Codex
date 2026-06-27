#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import ipaddress
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from license_agent.geolocation import GeoLite2GeoLocator, geolocation_cache_record
from license_agent.solo_analysis import find_all_csvs, read_csv_rows_many


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill an IP geolocation cache using MaxMind GeoLite2 City.")
    parser.add_argument(
        "--database",
        required=True,
        help="Path to GeoLite2-City.mmdb.",
    )
    parser.add_argument(
        "--aws-usage-summary",
        default="local_data/curated/aws_usage/company_usage_summary.json",
        help="Curated AWS usage summary JSON.",
    )
    parser.add_argument(
        "--solo-activation-root",
        default="local_data/raw/solo_softwarekey/activation_data",
        help="Folder containing SOLO ActivationDataExport CSV files.",
    )
    parser.add_argument(
        "--output-json",
        default="local_data/curated/ip_geolocation/ip_geolocation_cache.json",
        help="Output cache JSON path.",
    )
    parser.add_argument(
        "--output-csv",
        default="local_data/curated/ip_geolocation/ip_geolocation_cache.csv",
        help="Output cache CSV path.",
    )
    args = parser.parse_args()

    database_path = Path(args.database)
    if not database_path.exists():
        raise SystemExit(f"GeoLite2 database not found: {database_path}")

    ip_sources = collect_ip_sources(
        aws_usage_summary=Path(args.aws_usage_summary),
        solo_activation_root=Path(args.solo_activation_root),
    )
    locator = GeoLite2GeoLocator(database_path, provider_version=database_path.name)
    records: dict[str, dict[str, object]] = {}
    for ip_address in sorted(ip_sources):
        location = locator.lookup(ip_address)
        records[ip_address] = geolocation_cache_record(
            ip_address,
            location,
            source=",".join(sorted(ip_sources[ip_address])),
            provider_version=database_path.name,
        )

    payload = {
        "meta": {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "provider": "maxmind_geolite2_city",
            "provider_version": database_path.name,
            "source_count": len(ip_sources),
            "found_count": sum(1 for record in records.values() if record.get("lookup_status") == "found"),
            "not_found_count": sum(1 for record in records.values() if record.get("lookup_status") != "found"),
            "sources": ["aws_usage_summary", "solo_activation_exports"],
        },
        "ips": records,
    }
    write_json(payload, Path(args.output_json))
    write_csv(records.values(), Path(args.output_csv))
    print(args.output_json)
    print(args.output_csv)
    print(json.dumps(payload["meta"], sort_keys=True))


def collect_ip_sources(*, aws_usage_summary: Path, solo_activation_root: Path) -> dict[str, set[str]]:
    ip_sources: dict[str, set[str]] = defaultdict(set)
    if aws_usage_summary.exists():
        payload = json.loads(aws_usage_summary.read_text(encoding="utf-8"))
        for company in (payload.get("companies") or {}).values():
            for ip_address in company.get("public_ips") or []:
                add_public_ip(ip_sources, str(ip_address), "aws_processinfo")

    activation_paths = find_all_csvs(solo_activation_root)
    if activation_paths:
        for row in read_csv_rows_many(activation_paths, dedupe=True):
            add_public_ip(ip_sources, row.get("IPAddress") or "", "solo_activation")
    return ip_sources


def add_public_ip(ip_sources: dict[str, set[str]], value: str, source: str) -> None:
    ip_address = value.strip()
    if not ip_address:
        return
    try:
        parsed = ipaddress.ip_address(ip_address)
    except ValueError:
        return
    if parsed.is_private or parsed.is_loopback or parsed.is_link_local or parsed.is_multicast or parsed.is_reserved:
        return
    ip_sources[str(parsed)].add(source)


def write_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(records: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "ip_address",
        "source",
        "provider",
        "provider_version",
        "lookup_status",
        "lookup_date",
        "city",
        "region",
        "country",
        "latitude",
        "longitude",
        "accuracy_radius_km",
        "confidence_notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in fieldnames})


if __name__ == "__main__":
    main()
