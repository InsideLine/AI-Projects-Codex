from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.request import Request, urlopen

from .settings import LicenseAgentSettings
from .zoho import ZohoClient


VIOLATION_STAGES = frozenset(
    {
        "with rep",
        "with vps",
        "legal",
        "outside council",
        "invoice sent",
        "paid",
    }
)
NOT_VIOLATION_STAGES = frozenset({"not a license violation"})
CORPORATE_SUFFIXES = (
    " incorporated",
    " pty ltd",
    " inc",
    " corporation",
    " corp",
    " company",
    " co",
    " limited",
    " ltd",
    " llc",
    " lp",
    " plc",
    " gmbh",
    " ag",
    " sa",
    " bv",
    " nv",
    " pty",
    " sarl",
    " spa",
    " oy",
    " ab",
    " as",
    " aps",
)
VIOLATION_STATUS_MARKERS = (
    "confirmed lv",
    "sent to legal",
    "invoice sent",
    "sale processed",
    "sold!",
    "customer approved",
    "suspend their access",
    "breach",
)
NOT_VIOLATION_STATUS_MARKERS = (
    "not a lv",
    "not lv",
    "not an lv",
    "not a license violation",
)


@dataclass(frozen=True)
class ZohoVerificationRecord:
    record_id: str
    company_name: str
    company_key: str
    stage: str
    current_status: str
    classification: str
    modified_time: str


@dataclass(frozen=True)
class SalesRoutingViolationRecord:
    record_id: str
    purchasing_site_id: str
    company_name: str
    company_key: str
    license_id: str
    license_violation: str
    modified_time: str


@dataclass
class LicenseUsageAggregate:
    company_names: Counter[str]
    mac_addresses: set[str]
    machine_names: set[str]
    row_count: int = 0


@dataclass(frozen=True)
class CorrelatedCompany:
    company_key: str
    zoho_company_name: str
    process_company_name: str | None
    classification: str
    licenses_with_multiple_mac_addresses: int
    licenses_with_multiple_machine_names: int
    sample_license_ids: tuple[str, ...]


def fetch_zoho_view_csv(settings: LicenseAgentSettings, view_id: str) -> str:
    client = ZohoClient(settings)
    access_token = client.refresh_access_token()["access_token"]
    headers = {"Authorization": f"Zoho-oauthtoken {access_token}"}
    if settings.zoho_analytics_org_id:
        headers["ZANALYTICS-ORGID"] = settings.zoho_analytics_org_id
    url = f"https://analyticsapi.zoho.com/restapi/v2/workspaces/{settings.zoho_analytics_workspace_id}/views/{view_id}/data"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=120) as response:  # noqa: S310
        return response.read().decode("utf-8-sig", "replace")


def parse_zoho_verifications(csv_text: str) -> list[ZohoVerificationRecord]:
    rows: list[ZohoVerificationRecord] = []
    for row in csv.DictReader(io.StringIO(csv_text)):
        company_name = (row.get("Company Name") or "").strip()
        if not company_name:
            continue
        stage = (row.get("Stage") or "").strip()
        current_status = (row.get("Current Status") or "").strip()
        rows.append(
            ZohoVerificationRecord(
                record_id=(row.get("Id") or "").strip(),
                company_name=company_name,
                company_key=normalize_company_name(company_name),
                stage=stage,
                current_status=current_status,
                classification=classify_verification(stage=stage, current_status=current_status),
                modified_time=(row.get("Modified Time") or "").strip(),
            )
        )
    return rows


def parse_sites(csv_text: str) -> dict[str, str]:
    site_to_company: dict[str, str] = {}
    for row in csv.DictReader(io.StringIO(csv_text)):
        site_id = (row.get("Id") or "").strip()
        if not site_id:
            continue
        company_name = derive_company_name_from_site_row(row)
        if company_name:
            site_to_company[site_id] = company_name
    return site_to_company


def parse_zoho_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    formats = (
        "%b %d, %Y %I:%M %p",
        "%B %d, %Y %I:%M %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    )
    for fmt in formats:
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def parse_sales_routing_violations(
    csv_text: str,
    site_to_company: dict[str, str],
    *,
    min_created_time: datetime | None = None,
) -> list[SalesRoutingViolationRecord]:
    rows: list[SalesRoutingViolationRecord] = []
    for row in csv.DictReader(io.StringIO(csv_text)):
        license_violation = (row.get("License Violation") or "").strip()
        if license_violation.lower() != "yes":
            continue
        created_time = parse_zoho_datetime(row.get("Created Time"))
        if min_created_time is not None and (created_time is None or created_time < min_created_time):
            continue

        purchasing_site_id = (row.get("Purchasing Site") or "").strip()
        company_name = site_to_company.get(purchasing_site_id)
        if not company_name:
            continue

        company_key = normalize_company_name(company_name)
        if not company_key:
            continue

        rows.append(
            SalesRoutingViolationRecord(
                record_id=(row.get("Id") or "").strip(),
                purchasing_site_id=purchasing_site_id,
                company_name=company_name,
                company_key=company_key,
                license_id=(row.get("License ID") or "").strip(),
                license_violation=license_violation,
                modified_time=(row.get("Modified Time") or "").strip(),
            )
        )
    return rows


def classify_verification(*, stage: str, current_status: str) -> str:
    normalized_stage = stage.strip().lower()
    normalized_status = " ".join(current_status.lower().split())

    if normalized_stage in NOT_VIOLATION_STAGES:
        return "not_violation"
    if any(marker in normalized_status for marker in NOT_VIOLATION_STATUS_MARKERS):
        return "not_violation"

    if normalized_stage in VIOLATION_STAGES:
        return "violation"
    if any(marker in normalized_status for marker in VIOLATION_STATUS_MARKERS):
        return "violation"

    return "in_review"


def normalize_company_name(value: str) -> str:
    normalized = value.lower().replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    normalized = re.sub(r"\bthe\b", " ", normalized)
    normalized = " ".join(normalized.split())
    for suffix in CORPORATE_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
            break
    return " ".join(normalized.split())


def derive_company_name_from_site_row(row: dict[str, str]) -> str | None:
    company_name = (row.get("Company Name") or "").strip()
    if company_name:
        return company_name
    site_name = (row.get("Site Name") or "").strip()
    if "|" in site_name:
        return site_name.split("|", 1)[0].strip()
    return site_name or None


def summarize_processinfo_usage(raw_root: str | Path) -> dict[str, dict[str, LicenseUsageAggregate]]:
    root = Path(raw_root)
    aggregates: dict[str, dict[str, LicenseUsageAggregate]] = defaultdict(dict)

    for records_path in root.glob("aws_dynamodb_full/processinfo/*/*/*/*/records.jsonl"):
        with records_path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if "CompanyName" not in row or "LicenseId" not in row:
                    continue

                company_name = _attribute_value(row.get("CompanyName"))
                license_id = _attribute_value(row.get("LicenseId"))
                if not company_name or not license_id:
                    continue

                company_key = normalize_company_name(company_name)
                if not company_key:
                    continue

                company_licenses = aggregates[company_key]
                aggregate = company_licenses.get(license_id)
                if aggregate is None:
                    aggregate = LicenseUsageAggregate(company_names=Counter(), mac_addresses=set(), machine_names=set())
                    company_licenses[license_id] = aggregate

                aggregate.row_count += 1
                aggregate.company_names[company_name] += 1

                mac_address = _attribute_value(row.get("MacAddress"))
                machine_name = _attribute_value(row.get("MachineName"))
                if mac_address:
                    aggregate.mac_addresses.add(mac_address)
                if machine_name:
                    aggregate.machine_names.add(machine_name)

    return dict(aggregates)


def correlate_violation_companies(
    zoho_verifications: Iterable[ZohoVerificationRecord],
    process_usage: dict[str, dict[str, LicenseUsageAggregate]],
) -> dict[str, object]:
    latest_by_company: dict[str, ZohoVerificationRecord] = {}
    for record in zoho_verifications:
        previous = latest_by_company.get(record.company_key)
        if previous is None or record.modified_time >= previous.modified_time:
            latest_by_company[record.company_key] = record

    correlated: list[CorrelatedCompany] = []
    violation_company_count = 0
    matched_violation_company_count = 0

    for company_key, record in latest_by_company.items():
        if record.classification != "violation":
            continue
        violation_company_count += 1

        license_map = process_usage.get(company_key, {})
        licenses_with_multiple_mac_addresses = 0
        licenses_with_multiple_machine_names = 0
        sample_license_ids: list[str] = []
        process_company_name: str | None = None

        for license_id, aggregate in license_map.items():
            if process_company_name is None and aggregate.company_names:
                process_company_name = aggregate.company_names.most_common(1)[0][0]
            multi_mac = len(aggregate.mac_addresses) > 1
            multi_machine = len(aggregate.machine_names) > 1
            if multi_mac:
                licenses_with_multiple_mac_addresses += 1
            if multi_machine:
                licenses_with_multiple_machine_names += 1
            if (multi_mac or multi_machine) and len(sample_license_ids) < 10:
                sample_license_ids.append(license_id)

        if licenses_with_multiple_mac_addresses or licenses_with_multiple_machine_names:
            matched_violation_company_count += 1

        correlated.append(
            CorrelatedCompany(
                company_key=company_key,
                zoho_company_name=record.company_name,
                process_company_name=process_company_name,
                classification=record.classification,
                licenses_with_multiple_mac_addresses=licenses_with_multiple_mac_addresses,
                licenses_with_multiple_machine_names=licenses_with_multiple_machine_names,
                sample_license_ids=tuple(sample_license_ids),
            )
        )

    multi_machine_companies: list[dict[str, object]] = []
    total_process_companies_with_multi_usage = 0
    for company_key, license_map in process_usage.items():
        multi_license_ids: list[str] = []
        total_multi_mac = 0
        total_multi_machine = 0
        for license_id, aggregate in license_map.items():
            multi_mac = len(aggregate.mac_addresses) > 1
            multi_machine = len(aggregate.machine_names) > 1
            if multi_mac:
                total_multi_mac += 1
            if multi_machine:
                total_multi_machine += 1
            if (multi_mac or multi_machine) and len(multi_license_ids) < 10:
                multi_license_ids.append(license_id)
        if total_multi_mac or total_multi_machine:
            total_process_companies_with_multi_usage += 1
            process_company_name = None
            for aggregate in license_map.values():
                if aggregate.company_names:
                    process_company_name = aggregate.company_names.most_common(1)[0][0]
                    break
            matching_zoho = latest_by_company.get(company_key)
            multi_machine_companies.append(
                {
                    "company_key": company_key,
                    "process_company_name": process_company_name,
                    "zoho_company_name": matching_zoho.company_name if matching_zoho else None,
                    "zoho_classification": matching_zoho.classification if matching_zoho else None,
                    "licenses_with_multiple_mac_addresses": total_multi_mac,
                    "licenses_with_multiple_machine_names": total_multi_machine,
                    "sample_license_ids": multi_license_ids,
                }
            )

    return {
        "violation_companies": sorted(correlated, key=lambda item: item.zoho_company_name.lower()),
        "violation_company_count": violation_company_count,
        "matched_violation_company_count": matched_violation_company_count,
        "total_process_companies_with_multi_usage": total_process_companies_with_multi_usage,
        "multi_machine_companies": sorted(
            multi_machine_companies,
            key=lambda item: ((item["zoho_classification"] or ""), (item["process_company_name"] or "")),
        ),
    }


def correlate_company_keys(
    violator_companies: dict[str, str],
    process_usage: dict[str, dict[str, LicenseUsageAggregate]],
) -> dict[str, object]:
    correlated: list[CorrelatedCompany] = []
    matched_violation_company_count = 0

    for company_key, company_name in sorted(violator_companies.items(), key=lambda item: item[1].lower()):
        license_map = process_usage.get(company_key, {})
        licenses_with_multiple_mac_addresses = 0
        licenses_with_multiple_machine_names = 0
        sample_license_ids: list[str] = []
        process_company_name: str | None = None

        for license_id, aggregate in license_map.items():
            if process_company_name is None and aggregate.company_names:
                process_company_name = aggregate.company_names.most_common(1)[0][0]
            multi_mac = len(aggregate.mac_addresses) > 1
            multi_machine = len(aggregate.machine_names) > 1
            if multi_mac:
                licenses_with_multiple_mac_addresses += 1
            if multi_machine:
                licenses_with_multiple_machine_names += 1
            if (multi_mac or multi_machine) and len(sample_license_ids) < 10:
                sample_license_ids.append(license_id)

        if licenses_with_multiple_mac_addresses or licenses_with_multiple_machine_names:
            matched_violation_company_count += 1

        correlated.append(
            CorrelatedCompany(
                company_key=company_key,
                zoho_company_name=company_name,
                process_company_name=process_company_name,
                classification="violation",
                licenses_with_multiple_mac_addresses=licenses_with_multiple_mac_addresses,
                licenses_with_multiple_machine_names=licenses_with_multiple_machine_names,
                sample_license_ids=tuple(sample_license_ids),
            )
        )

    multi_machine_companies: list[dict[str, object]] = []
    total_process_companies_with_multi_usage = 0
    for company_key, license_map in process_usage.items():
        multi_license_ids: list[str] = []
        total_multi_mac = 0
        total_multi_machine = 0
        for license_id, aggregate in license_map.items():
            multi_mac = len(aggregate.mac_addresses) > 1
            multi_machine = len(aggregate.machine_names) > 1
            if multi_mac:
                total_multi_mac += 1
            if multi_machine:
                total_multi_machine += 1
            if (multi_mac or multi_machine) and len(multi_license_ids) < 10:
                multi_license_ids.append(license_id)
        if total_multi_mac or total_multi_machine:
            total_process_companies_with_multi_usage += 1
            process_company_name = None
            for aggregate in license_map.values():
                if aggregate.company_names:
                    process_company_name = aggregate.company_names.most_common(1)[0][0]
                    break
            zoho_company_name = violator_companies.get(company_key)
            multi_machine_companies.append(
                {
                    "company_key": company_key,
                    "process_company_name": process_company_name,
                    "zoho_company_name": zoho_company_name,
                    "zoho_classification": "violation" if zoho_company_name else None,
                    "licenses_with_multiple_mac_addresses": total_multi_mac,
                    "licenses_with_multiple_machine_names": total_multi_machine,
                    "sample_license_ids": multi_license_ids,
                }
            )

    return {
        "violation_companies": correlated,
        "violation_company_count": len(violator_companies),
        "matched_violation_company_count": matched_violation_company_count,
        "total_process_companies_with_multi_usage": total_process_companies_with_multi_usage,
        "multi_machine_companies": sorted(
            multi_machine_companies,
            key=lambda item: ((item["zoho_classification"] or ""), (item["process_company_name"] or "")),
        ),
    }


def write_csv(path: str | Path, fieldnames: list[str], rows: Iterable[dict[str, object]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_markdown_report(
    path: str | Path,
    *,
    generated_at: datetime,
    violation_company_count: int,
    matched_violation_company_count: int,
    total_process_companies_with_multi_usage: int,
    correlated_companies: Iterable[CorrelatedCompany],
) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# License Violation Correlation Report",
        "",
        f"Generated at: {generated_at.astimezone(timezone.utc).isoformat()}",
        "",
        f"- Zoho violation companies: {violation_company_count}",
        f"- Violation companies with multi-machine or multi-MAC license usage in ProcessInfo: {matched_violation_company_count}",
        f"- Total ProcessInfo companies with multi-machine or multi-MAC license usage: {total_process_companies_with_multi_usage}",
        "",
        "## Matched Violation Companies",
        "",
        "| Zoho Company | ProcessInfo Company | Multi-MAC Licenses | Multi-Machine Licenses | Sample License IDs |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for company in correlated_companies:
        if not company.licenses_with_multiple_mac_addresses and not company.licenses_with_multiple_machine_names:
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    company.zoho_company_name,
                    company.process_company_name or "",
                    str(company.licenses_with_multiple_mac_addresses),
                    str(company.licenses_with_multiple_machine_names),
                    ", ".join(company.sample_license_ids),
                ]
            )
            + " |"
        )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _attribute_value(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("S", "N"):
        field_value = value.get(key)
        if isinstance(field_value, str) and field_value.strip():
            return field_value.strip()
    return None
