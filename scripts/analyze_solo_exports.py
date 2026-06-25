from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from license_agent.correlation_analysis import normalize_company_name
from license_agent.solo_analysis import (
    build_company_metrics,
    find_all_csvs,
    find_latest_csv,
    load_license_verification_cutoffs,
    read_csv_rows,
    read_csv_rows_many,
    summarize_metrics,
)


SAMPLE_LICENSE_ID = "66275132"
SAMPLE_COMPANY_NAME = "Hudson Housing Capital LLC"


def main() -> None:
    generated_at = datetime.now(timezone.utc)
    batch_id = generated_at.strftime("%Y%m%dT%H%M%SZ")

    export_root = Path("local_data/raw/solo_softwarekey/export_licenses")
    activation_root = Path("local_data/raw/solo_softwarekey/activation_data")
    export_path = find_latest_csv(export_root)
    activation_paths = find_all_csvs(activation_root)
    lv_path = Path("local_data/raw/zoho_analytics/license_verifications/2026/06/19/20260619T094707Z/license_verifications.csv")
    srf_recent_path = Path("local_data/analysis/sales_routing_violation_correlation/20260619T184829Z/violation_companies.csv")
    srf_broad_path = Path("local_data/analysis/sales_routing_violation_correlation/20260619T133639Z/violation_companies.csv")

    if export_path is None:
        raise FileNotFoundError("No SOLO export licenses CSV files found.")
    if not activation_paths:
        raise FileNotFoundError("No SOLO activation CSV files found.")

    export_rows = read_csv_rows(export_path)
    activation_rows = read_csv_rows_many(activation_paths, dedupe=True)
    lv_cutoffs, lv_names = load_license_verification_cutoffs(lv_path)

    recent_srf_keys = load_company_keys_from_csv(srf_recent_path, "sales_routing_company_name")
    broad_srf_keys = load_company_keys_from_csv(srf_broad_path, "sales_routing_company_name")

    all_metrics = build_company_metrics(activation_rows)
    lv_metrics = build_company_metrics(activation_rows, cutoff_by_company=lv_cutoffs)

    recent_srf_overlap = [key for key in recent_srf_keys if key in all_metrics]
    broad_srf_overlap = [key for key in broad_srf_keys if key in all_metrics]
    lv_overlap = [key for key in lv_names if key in lv_metrics]

    all_summary = summarize_metrics(all_metrics, all_metrics.keys())
    lv_summary = summarize_metrics(lv_metrics, lv_overlap)
    broad_srf_summary = summarize_metrics(all_metrics, broad_srf_overlap)

    outpoints = build_outpoints(all_summary, lv_summary)

    sample_license = build_sample_license_report(
        export_rows=export_rows,
        activation_rows=activation_rows,
        lv_cutoffs=lv_cutoffs,
        sample_license_id=SAMPLE_LICENSE_ID,
    )

    analysis_dir = Path("local_data/analysis/solo_signal_review") / batch_id
    analysis_dir.mkdir(parents=True, exist_ok=True)

    cohort_payload = {
        "generated_at": generated_at.isoformat(),
        "solo_export_path": str(export_path),
        "solo_activation_paths": [str(path) for path in activation_paths],
        "solo_activation_path_count": len(activation_paths),
        "solo_export_license_count": len({row["LicenseID"] for row in export_rows if row.get("LicenseID")}),
        "solo_activation_license_count": len({row["LicenseID"] for row in activation_rows if row.get("LicenseID")}),
        "solo_activation_company_count": len(all_metrics),
        "recent_srf_overlap_count": len(recent_srf_overlap),
        "broad_srf_overlap_count": len(broad_srf_overlap),
        "license_verification_overlap_count": len(lv_overlap),
        "recent_srf_overlap_companies": recent_srf_overlap,
        "broad_srf_overlap_companies": broad_srf_overlap,
        "license_verification_overlap_companies": [
            {"company_key": key, "company_name": lv_names[key]} for key in lv_overlap
        ],
        "general_population_summary": all_summary,
        "broad_srf_overlap_summary": broad_srf_summary,
        "license_verification_overlap_summary": lv_summary,
        "outpoints": outpoints,
    }
    (analysis_dir / "cohort_report.json").write_text(json.dumps(cohort_payload, indent=2, sort_keys=True), encoding="utf-8")
    (analysis_dir / "sample_license_report.json").write_text(
        json.dumps(sample_license, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (analysis_dir / "cohort_report.md").write_text(render_cohort_markdown(cohort_payload), encoding="utf-8")
    (analysis_dir / "sample_license_report.md").write_text(render_sample_markdown(sample_license), encoding="utf-8")

    print(
        json.dumps(
            {
                "analysis_dir": str(analysis_dir),
                "cohort_report_json": str(analysis_dir / "cohort_report.json"),
                "sample_license_report_json": str(analysis_dir / "sample_license_report.json"),
                "recent_srf_overlap_count": len(recent_srf_overlap),
                "broad_srf_overlap_count": len(broad_srf_overlap),
                "license_verification_overlap_count": len(lv_overlap),
                "sample_license_id": SAMPLE_LICENSE_ID,
                "sample_company_name": SAMPLE_COMPANY_NAME,
            },
            indent=2,
            sort_keys=True,
        )
    )


def load_company_keys_from_csv(path: Path, column_name: str) -> set[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            normalize_company_name(row[column_name])
            for row in csv.DictReader(handle)
            if row.get(column_name)
        }


def build_outpoints(
    all_summary: dict[str, float | int] | None,
    violator_summary: dict[str, float | int] | None,
) -> list[str]:
    if not all_summary or not violator_summary:
        return []

    outpoints: list[str] = []
    if violator_summary["share_with_rejections"] > all_summary["share_with_rejections"]:
        outpoints.append(
            "Rejected activations are materially more common in the overlapping violator cohort."
        )
    if violator_summary["median_rejection_rate"] > all_summary["median_rejection_rate"]:
        outpoints.append(
            "The median rejected-activation rate is higher in the overlapping violator cohort."
        )
    if violator_summary["share_with_multi_ip"] > all_summary["share_with_multi_ip"]:
        outpoints.append(
            "Multiple activation IPs per company appear more often in the overlapping violator cohort."
        )
    if violator_summary["share_with_multi_installation"] > all_summary["share_with_multi_installation"]:
        outpoints.append(
            "Multiple installation IDs per company appear more often in the overlapping violator cohort."
        )
    if not outpoints:
        outpoints.append("No clear SOLO-only outpoint was stronger than the general population in this slice.")
    return outpoints


def build_sample_license_report(
    *,
    export_rows: list[dict[str, str]],
    activation_rows: list[dict[str, str]],
    lv_cutoffs: dict[str, datetime],
    sample_license_id: str,
) -> dict[str, object]:
    export_row = next(row for row in export_rows if row.get("LicenseID") == sample_license_id)
    sample_company_key = normalize_company_name(export_row["CompanyName"])
    violation_cutoff = lv_cutoffs.get(sample_company_key)

    license_activation_rows = [row for row in activation_rows if row.get("LicenseID") == sample_license_id]
    if violation_cutoff is not None:
        license_activation_rows = [
            row
            for row in license_activation_rows
            if (activation_date := _parse_iso_datetime(row.get("ActivationDate"))) is not None and activation_date < violation_cutoff
        ]

    activation_dates = [_parse_iso_datetime(row.get("ActivationDate")) for row in license_activation_rows]
    activation_dates = [value for value in activation_dates if value is not None]
    activation_ips = sorted({row["IPAddress"] for row in license_activation_rows if row.get("IPAddress")})
    activation_versions = sorted({row["InitialProductVersion"] for row in license_activation_rows if row.get("InitialProductVersion")})
    activation_geo = [lookup_ipinfo(ip_address) for ip_address in activation_ips]

    process_summary = aggregate_processinfo_for_license(sample_license_id, cutoff=violation_cutoff)
    personnel_licensed = extract_personnel_from_notes(export_row.get("LicenseNotes") or "")

    sample_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "license_id": sample_license_id,
        "company_name": export_row.get("CompanyName"),
        "violation_cutoff": violation_cutoff.isoformat() if violation_cutoff else None,
        "solo_license": {
            "license_entered_date": export_row.get("LicenseEnteredDate"),
            "status": (export_row.get("Status") or "").strip(),
            "product_name": export_row.get("ProductName"),
            "option_name": export_row.get("OptionName"),
            "license_notes": export_row.get("LicenseNotes"),
            "personnel_licensed_proxy": personnel_licensed,
        },
        "solo_activations": {
            "activation_count": len(license_activation_rows),
            "first_activation_date": min(activation_dates).isoformat() if activation_dates else None,
            "last_activation_date": max(activation_dates).isoformat() if activation_dates else None,
            "successful_activations": sum(1 for row in license_activation_rows if row.get("Status") == "Successful"),
            "rejected_activations": sum(1 for row in license_activation_rows if row.get("Status") == "Rejected"),
            "deactivations": sum(1 for row in license_activation_rows if row.get("DeactivatedDate")),
            "unique_ip_count": len(activation_ips),
            "unique_installation_count": len({row["InstallationID"] for row in license_activation_rows if row.get("InstallationID")}),
            "ip_addresses": activation_ips,
            "ip_geolocation": activation_geo,
            "initial_versions": activation_versions,
        },
        "aws_usage": process_summary,
        "findings": build_sample_findings(
            export_row=export_row,
            activation_dates=activation_dates,
            personnel_licensed=personnel_licensed,
            process_summary=process_summary,
        ),
    }
    return sample_payload


def aggregate_processinfo_for_license(license_id: str, *, cutoff: datetime | None) -> dict[str, object]:
    raw_root = Path("local_data/raw/aws_dynamodb_full/processinfo")
    total_bytes = 0
    total_files = 0
    total_links = 0
    machines: Counter[str] = Counter()
    macs: Counter[str] = Counter()
    users: Counter[str] = Counter()
    ips: Counter[str] = Counter()
    companies: Counter[str] = Counter()
    starts: list[str] = []
    ends: list[str] = []

    for records_path in raw_root.glob("*/*/*/*/records.jsonl"):
        with records_path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if (row.get("LicenseId") or {}).get("N") != license_id:
                    continue
                start_time = (row.get("StartTime") or {}).get("S")
                if cutoff is not None and start_time:
                    parsed_start = _parse_sql_datetime(start_time)
                    if parsed_start is not None and parsed_start >= cutoff:
                        continue

                total_bytes += int((row.get("FileSizeInBytes") or {}).get("N") or 0)
                total_files += int((row.get("NumFilesProcessed") or {}).get("N") or 0)
                total_links += int((row.get("NumLinksProcessed") or {}).get("N") or 0)

                for key, counter in (
                    ("MachineName", machines),
                    ("MacAddress", macs),
                    ("UserName", users),
                    ("PublicIPAddress", ips),
                    ("CompanyName", companies),
                ):
                    value = (row.get(key) or {}).get("S")
                    if value:
                        counter[value] += 1

                end_time = (row.get("EndTime") or {}).get("S")
                if start_time:
                    starts.append(start_time)
                if end_time:
                    ends.append(end_time)

    ip_geolocation = [lookup_ipinfo(ip_address) for ip_address in sorted(ips)]
    return {
        "total_file_size_gb": round(total_bytes / (1024**3), 2),
        "total_files_processed": total_files,
        "total_links_processed": total_links,
        "first_usage_start": min(starts) if starts else None,
        "last_usage_end": max(ends) if ends else None,
        "unique_machine_count": len(machines),
        "unique_mac_count": len(macs),
        "unique_user_count": len(users),
        "unique_ip_count": len(ips),
        "company_names": companies.most_common(5),
        "top_machines": machines.most_common(10),
        "top_macs": macs.most_common(10),
        "top_users": users.most_common(10),
        "top_ips": ips.most_common(10),
        "ip_geolocation": ip_geolocation,
    }


def build_sample_findings(
    *,
    export_row: dict[str, str],
    activation_dates: list[datetime],
    personnel_licensed: int | None,
    process_summary: dict[str, object],
) -> list[str]:
    findings: list[str] = []
    first_usage = _parse_sql_datetime(process_summary.get("first_usage_start"))
    if activation_dates and first_usage and first_usage < min(activation_dates):
        delay_days = (min(activation_dates) - first_usage).days
        findings.append(
            f"First AWS usage predates the first SOLO activation in this export by about {delay_days} days, which likely indicates missing earlier SOLO history or a mismatch that needs review."
        )

    if (process_summary.get("unique_machine_count") or 0) > 1:
        findings.append(
            f"AWS usage before the violation cutoff spans {process_summary['unique_machine_count']} machine names and {process_summary['unique_mac_count']} MAC addresses on the same license."
        )

    if personnel_licensed and process_summary.get("total_file_size_gb") is not None:
        gb_per_person = process_summary["total_file_size_gb"] / personnel_licensed
        if gb_per_person > 100:
            findings.append(
                f"Usage is about {gb_per_person:.1f} GB per licensed person, well above the 100 GB review threshold."
            )

    if process_summary.get("unique_ip_count") == 1:
        findings.append("Observed AWS usage IPs are concentrated on one public IP.")
    elif (process_summary.get("unique_ip_count") or 0) > 1:
        findings.append(
            f"Observed AWS usage spans {process_summary['unique_ip_count']} public IPs, though the sample geolocation stays within New York City."
        )

    if not findings:
        findings.append("No standout signal was derived from the current SOLO and AWS slice.")
    return findings


def extract_personnel_from_notes(notes: str) -> int | None:
    match = re.search(r"up to\s+(\d+)\s+personnel", notes, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def lookup_ipinfo(ip_address: str) -> dict[str, str]:
    request = Request(f"https://ipinfo.io/{ip_address}/json")
    with urlopen(request, timeout=20) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    return {
        "ip": ip_address,
        "city": payload.get("city"),
        "region": payload.get("region"),
        "country": payload.get("country"),
        "org": payload.get("org"),
    }


def render_cohort_markdown(payload: dict[str, object]) -> str:
    lines = [
        "# SOLO Signal Review",
        "",
        f"Generated at: {payload['generated_at']}",
        "",
        "## Coverage",
        "",
        f"- SOLO export licenses: {payload['solo_export_license_count']}",
        f"- SOLO activation licenses: {payload['solo_activation_license_count']}",
        f"- SOLO activation companies: {payload['solo_activation_company_count']}",
        f"- Recent SRF violator overlap: {payload['recent_srf_overlap_count']}",
        f"- Broader SRF violator overlap: {payload['broad_srf_overlap_count']}",
        f"- License Verification violator overlap: {payload['license_verification_overlap_count']}",
        "",
        "## Outpoints",
        "",
    ]
    for item in payload["outpoints"]:
        lines.append(f"- {item}")

    all_summary = payload.get("general_population_summary") or {}
    lv_summary = payload.get("license_verification_overlap_summary") or {}
    lines.extend(
        [
            "",
            "## Comparison",
            "",
            f"- General population median activations: {all_summary.get('median_activations')}",
            f"- Violator overlap median activations: {lv_summary.get('median_activations')}",
            f"- General population rejection share: {_pct(all_summary.get('share_with_rejections'))}",
            f"- Violator overlap rejection share: {_pct(lv_summary.get('share_with_rejections'))}",
            f"- General population multi-IP share: {_pct(all_summary.get('share_with_multi_ip'))}",
            f"- Violator overlap multi-IP share: {_pct(lv_summary.get('share_with_multi_ip'))}",
            f"- General population multi-installation share: {_pct(all_summary.get('share_with_multi_installation'))}",
            f"- Violator overlap multi-installation share: {_pct(lv_summary.get('share_with_multi_installation'))}",
        ]
    )
    return "\n".join(lines) + "\n"


def render_sample_markdown(payload: dict[str, object]) -> str:
    solo = payload["solo_license"]
    activations = payload["solo_activations"]
    aws = payload["aws_usage"]
    lines = [
        f"# Sample License Report: {payload['license_id']}",
        "",
        f"Company: {payload['company_name']}",
        f"Violation cutoff used: {payload['violation_cutoff']}",
        "",
        "## SOLO",
        "",
        f"- License entered: {solo['license_entered_date']}",
        f"- License status: {solo['status']}",
        f"- Personnel licensed proxy from notes: {solo['personnel_licensed_proxy']}",
        f"- Activation count before cutoff: {activations['activation_count']}",
        f"- First activation: {activations['first_activation_date']}",
        f"- Last activation: {activations['last_activation_date']}",
        f"- Successful activations: {activations['successful_activations']}",
        f"- Rejected activations: {activations['rejected_activations']}",
        f"- Deactivations: {activations['deactivations']}",
        f"- Unique activation IPs: {activations['unique_ip_count']}",
        "",
        "## AWS Usage",
        "",
        f"- First usage start: {aws['first_usage_start']}",
        f"- Last usage end: {aws['last_usage_end']}",
        f"- Total file size: {aws['total_file_size_gb']} GB",
        f"- Total files processed: {aws['total_files_processed']}",
        f"- Total links processed: {aws['total_links_processed']}",
        f"- Unique machines: {aws['unique_machine_count']}",
        f"- Unique MACs: {aws['unique_mac_count']}",
        f"- Unique users: {aws['unique_user_count']}",
        f"- Unique usage IPs: {aws['unique_ip_count']}",
        "",
        "## Findings",
        "",
    ]
    for finding in payload["findings"]:
        lines.append(f"- {finding}")
    return "\n".join(lines) + "\n"


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_sql_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _pct(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "n/a"
    return f"{value * 100:.1f}%"


if __name__ == "__main__":
    main()
