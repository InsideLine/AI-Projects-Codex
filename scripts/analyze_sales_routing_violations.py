from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from license_agent.correlation_analysis import (
    correlate_company_keys,
    fetch_zoho_view_csv,
    parse_sales_routing_violations,
    parse_sites,
    summarize_processinfo_usage,
    write_csv,
    write_markdown_report,
)
from license_agent.settings import LicenseAgentSettings, load_dotenv


SALES_ROUTING_VIEW_ID = "1738519000007992366"
SITES_VIEW_ID = "1738519000007630335"
DEFAULT_CUTOFF_DATE = datetime(2023, 6, 19)


def main() -> None:
    dotenv_path = Path(".env")
    if dotenv_path.exists():
        load_dotenv(dotenv_path)

    settings = LicenseAgentSettings.from_env(enable_secret_fallback=False)
    generated_at = datetime.now(timezone.utc)
    batch_id = generated_at.strftime("%Y%m%dT%H%M%SZ")

    sales_routing_csv = fetch_zoho_view_csv(settings, SALES_ROUTING_VIEW_ID)
    sites_csv = fetch_zoho_view_csv(settings, SITES_VIEW_ID)

    zoho_dir = Path("local_data/raw/zoho_analytics/sales_routing_violations") / generated_at.strftime("%Y/%m/%d") / batch_id
    zoho_dir.mkdir(parents=True, exist_ok=True)
    sales_routing_csv_path = zoho_dir / "sales_routing_forms.csv"
    sites_csv_path = zoho_dir / "sites.csv"
    sales_routing_csv_path.write_text(sales_routing_csv, encoding="utf-8")
    sites_csv_path.write_text(sites_csv, encoding="utf-8")

    site_to_company = parse_sites(sites_csv)
    violation_rows = parse_sales_routing_violations(
        sales_routing_csv,
        site_to_company,
        min_created_time=DEFAULT_CUTOFF_DATE,
    )
    latest_by_company: dict[str, str] = {}
    for row in violation_rows:
        latest_by_company[row.company_key] = row.company_name

    process_usage = summarize_processinfo_usage(settings.ingest_raw_root)
    correlation = correlate_company_keys(latest_by_company, process_usage)

    analysis_dir = Path("local_data/analysis/sales_routing_violation_correlation") / batch_id
    analysis_dir.mkdir(parents=True, exist_ok=True)

    report_payload = {
        "generated_at": generated_at.isoformat(),
        "cutoff_created_time": DEFAULT_CUTOFF_DATE.date().isoformat(),
        "sales_routing_csv_path": str(sales_routing_csv_path),
        "sites_csv_path": str(sites_csv_path),
        "process_raw_root": str(Path(settings.ingest_raw_root) / "aws_dynamodb_full" / "processinfo"),
        "sales_routing_violation_record_count": len(violation_rows),
        "sales_routing_violation_company_count": correlation["violation_company_count"],
        "matched_violation_company_count": correlation["matched_violation_company_count"],
        "total_process_companies_with_multi_usage": correlation["total_process_companies_with_multi_usage"],
        "violation_companies": [
            {
                "sales_routing_company_name": company.zoho_company_name,
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
            "sales_routing_company_name",
            "process_company_name",
            "licenses_with_multiple_mac_addresses",
            "licenses_with_multiple_machine_names",
            "sample_license_ids",
        ],
        (
            {
                "sales_routing_company_name": company.zoho_company_name,
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
            "sales_routing_company_name",
            "zoho_classification",
            "licenses_with_multiple_mac_addresses",
            "licenses_with_multiple_machine_names",
            "sample_license_ids",
        ],
        (
            {
                "process_company_name": row["process_company_name"] or "",
                "sales_routing_company_name": row["zoho_company_name"] or "",
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
                "cutoff_created_time": DEFAULT_CUTOFF_DATE.date().isoformat(),
                "report_json_path": str(report_json_path),
                "sales_routing_violation_record_count": len(violation_rows),
                "sales_routing_violation_company_count": correlation["violation_company_count"],
                "matched_violation_company_count": correlation["matched_violation_company_count"],
                "total_process_companies_with_multi_usage": correlation["total_process_companies_with_multi_usage"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
