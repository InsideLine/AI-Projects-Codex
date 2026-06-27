#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from license_agent.correlation_analysis import normalize_company_name
from license_agent.solo_analysis import build_company_metrics, find_all_csvs, read_csv_rows_many


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a curated SOLO activation summary keyed by normalized company.")
    parser.add_argument(
        "--activation-root",
        default="local_data/raw/solo_softwarekey/activation_data",
        help="Folder containing SOLO ActivationDataExport CSV files.",
    )
    parser.add_argument(
        "--export-root",
        default="local_data/raw/solo_softwarekey/export_licenses",
        help="Folder containing SOLO Export Licenses CSV files.",
    )
    parser.add_argument(
        "--output",
        default="local_data/curated/solo_softwarekey/company_activation_summary.json",
        help="Output JSON path.",
    )
    args = parser.parse_args()

    activation_root = Path(args.activation_root)
    paths = find_all_csvs(activation_root)
    rows = read_csv_rows_many(paths, dedupe=True)
    metrics = build_company_metrics(rows)
    export_root = Path(args.export_root)
    export_paths = find_all_csvs(export_root)
    export_rows = read_csv_rows_many(export_paths, dedupe=True)
    export_metrics = _solo_export_entitlement_metrics(export_rows)
    company_payload = {company_key: asdict(metric) for company_key, metric in sorted(metrics.items())}
    for company_key, export_metric in export_metrics.items():
        existing = company_payload.setdefault(
            company_key,
            {
                "company_key": company_key,
                "company_name": export_metric["company_name"],
                "activations": 0,
                "successful_activations": 0,
                "rejected_activations": 0,
                "rejection_rate": 0.0,
                "unique_ips": 0,
                "unique_installations": 0,
                "unique_computers": 0,
                "deactivations": 0,
                "unique_licenses": 0,
                "license_ids": [],
                "activation_ips": [],
            },
        )
        merged_license_ids = sorted(set(str(value) for value in existing.get("license_ids") or []) | set(str(value) for value in export_metric.get("license_ids") or []))
        merged_activation_ips = sorted(set(str(value) for value in existing.get("activation_ips") or []) | set(str(value) for value in export_metric.get("activation_ips") or []))
        existing.update(export_metric)
        existing["license_ids"] = merged_license_ids
        existing["activation_ips"] = merged_activation_ips
    by_license_id: dict[str, str] = {}
    for company_key, metric in company_payload.items():
        for license_id in metric.get("license_ids") or []:
            if license_id:
                by_license_id[str(license_id)] = company_key
    payload = {
        "meta": {
            "schema_version": 2,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "SOLO Activation Data Export CSV and Export Licenses CSV",
            "source_root": str(activation_root),
            "source_file_count": len(paths),
            "source_row_count": len(rows),
            "export_source_root": str(export_root),
            "export_source_file_count": len(export_paths),
            "export_source_row_count": len(export_rows),
            "company_count": len(company_payload),
        },
        "companies": dict(sorted(company_payload.items())),
        "by_license_id": dict(sorted(by_license_id.items())),
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output_path)

def _solo_export_entitlement_metrics(rows: list[dict[str, str]]) -> dict[str, dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    seen_license_ids: dict[str, set[str]] = {}
    for row in rows:
        company_name = (row.get("CompanyName") or "").strip()
        company_key = normalize_company_name(company_name)
        if not company_key:
            continue
        q_ordered = _positive_int(row.get("QOrdered"))
        metric = grouped.setdefault(
            company_key,
            {
                "company_key": company_key,
                "company_name": company_name,
                "q_ordered_total": 0,
                "q_ordered_max": 0,
                "q_ordered_license_count": 0,
                "solo_entitlement_count": None,
                "solo_entitlement_source": "",
                "license_ids": [],
                "activation_ips": [],
            },
        )
        if q_ordered is None:
            continue
        license_id = (row.get("LicenseID") or "").strip()
        license_ids = seen_license_ids.setdefault(company_key, set())
        if license_id and license_id in license_ids:
            continue
        if license_id:
            license_ids.add(license_id)
            license_id_list = metric.get("license_ids")
            if isinstance(license_id_list, list):
                license_id_list.append(license_id)
        metric["q_ordered_total"] = int(metric["q_ordered_total"]) + q_ordered
        metric["q_ordered_max"] = max(int(metric["q_ordered_max"]), q_ordered)
        metric["q_ordered_license_count"] = int(metric["q_ordered_license_count"]) + 1
    for metric in grouped.values():
        total = int(metric["q_ordered_total"])
        if total > 0:
            metric["solo_entitlement_count"] = total
            metric["solo_entitlement_source"] = "SOLO Export Licenses QOrdered total"
    return grouped


def _positive_int(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = int(float(str(value).replace(",", "").strip()))
    except ValueError:
        return None
    if number <= 0:
        return None
    return number


if __name__ == "__main__":
    main()
