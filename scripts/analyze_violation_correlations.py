from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from license_agent.correlation_analysis import (
    correlate_violation_companies,
    fetch_zoho_view_csv,
    parse_zoho_verifications,
    summarize_processinfo_usage,
    write_csv,
    write_markdown_report,
)
from license_agent.settings import LicenseAgentSettings, load_dotenv


LICENSE_VERIFICATIONS_VIEW_ID = "1738519000061515003"


def main() -> None:
    dotenv_path = Path(".env")
    if dotenv_path.exists():
        load_dotenv(dotenv_path)

    settings = LicenseAgentSettings.from_env(enable_secret_fallback=False)
    generated_at = datetime.now(timezone.utc)
    batch_id = generated_at.strftime("%Y%m%dT%H%M%SZ")

    zoho_csv = fetch_zoho_view_csv(settings, LICENSE_VERIFICATIONS_VIEW_ID)
    zoho_dir = Path("local_data/raw/zoho_analytics/license_verifications") / generated_at.strftime("%Y/%m/%d") / batch_id
    zoho_dir.mkdir(parents=True, exist_ok=True)
    zoho_csv_path = zoho_dir / "license_verifications.csv"
    zoho_csv_path.write_text(zoho_csv, encoding="utf-8")

    zoho_verifications = parse_zoho_verifications(zoho_csv)
    classification_counts = Counter(record.classification for record in zoho_verifications)
    process_usage = summarize_processinfo_usage(settings.ingest_raw_root)
    correlation = correlate_violation_companies(zoho_verifications, process_usage)

    analysis_dir = Path("local_data/analysis/license_violation_correlation") / batch_id
    analysis_dir.mkdir(parents=True, exist_ok=True)

    report_payload = {
        "generated_at": generated_at.isoformat(),
        "zoho_csv_path": str(zoho_csv_path),
        "process_raw_root": str(Path(settings.ingest_raw_root) / "aws_dynamodb_full" / "processinfo"),
        "zoho_record_count": len(zoho_verifications),
        "zoho_classification_counts": dict(classification_counts),
        "violation_company_count": correlation["violation_company_count"],
        "matched_violation_company_count": correlation["matched_violation_company_count"],
        "total_process_companies_with_multi_usage": correlation["total_process_companies_with_multi_usage"],
        "violation_companies": [
            {
                "zoho_company_name": company.zoho_company_name,
                "process_company_name": company.process_company_name,
                "licenses_with_multiple_mac_addresses": company.licenses_with_multiple_mac_addresses,
                "licenses_with_multiple_machine_names": company.licenses_with_multiple_machine_names,
                "sample_license_ids": list(company.sample_license_ids),
            }
            for company in correlation["violation_companies"]
        ],
    }

    report_json_path = analysis_dir / "report.json"
    report_json_path.write_text(json.dumps(report_payload, indent=2, sort_keys=True), encoding="utf-8")

    write_markdown_report(
        analysis_dir / "report.md",
        generated_at=generated_at,
        violation_company_count=correlation["violation_company_count"],
        matched_violation_company_count=correlation["matched_violation_company_count"],
        total_process_companies_with_multi_usage=correlation["total_process_companies_with_multi_usage"],
        correlated_companies=correlation["violation_companies"],
    )

    write_csv(
        analysis_dir / "violation_companies.csv",
        [
            "zoho_company_name",
            "process_company_name",
            "licenses_with_multiple_mac_addresses",
            "licenses_with_multiple_machine_names",
            "sample_license_ids",
        ],
        (
            {
                "zoho_company_name": company.zoho_company_name,
                "process_company_name": company.process_company_name or "",
                "licenses_with_multiple_mac_addresses": company.licenses_with_multiple_mac_addresses,
                "licenses_with_multiple_machine_names": company.licenses_with_multiple_machine_names,
                "sample_license_ids": ";".join(company.sample_license_ids),
            }
            for company in correlation["violation_companies"]
        ),
    )

    write_csv(
        analysis_dir / "multi_machine_companies.csv",
        [
            "process_company_name",
            "zoho_company_name",
            "zoho_classification",
            "licenses_with_multiple_mac_addresses",
            "licenses_with_multiple_machine_names",
            "sample_license_ids",
        ],
        (
            {
                "process_company_name": row["process_company_name"] or "",
                "zoho_company_name": row["zoho_company_name"] or "",
                "zoho_classification": row["zoho_classification"] or "",
                "licenses_with_multiple_mac_addresses": row["licenses_with_multiple_mac_addresses"],
                "licenses_with_multiple_machine_names": row["licenses_with_multiple_machine_names"],
                "sample_license_ids": ";".join(row["sample_license_ids"]),
            }
            for row in correlation["multi_machine_companies"]
        ),
    )

    print(
        json.dumps(
            {
                "analysis_dir": str(analysis_dir),
                "report_json_path": str(report_json_path),
                "violation_company_count": correlation["violation_company_count"],
                "matched_violation_company_count": correlation["matched_violation_company_count"],
                "total_process_companies_with_multi_usage": correlation["total_process_companies_with_multi_usage"],
                "zoho_record_count": len(zoho_verifications),
                "zoho_classification_counts": dict(classification_counts),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
