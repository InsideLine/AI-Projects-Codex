from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Iterable, Protocol

from .models import Activation, LicenseEntitlement, OrganizationDefinition, UsageRecord


class ActivationSource(Protocol):
    def fetch_activations(self, license_id: str | None = None, company_name: str | None = None) -> Iterable[Activation]:
        ...


class UsageSource(Protocol):
    def fetch_usage(self, license_id: str | None = None, company_name: str | None = None) -> Iterable[UsageRecord]:
        ...


class EntitlementSource(Protocol):
    def fetch_entitlements(self, license_id: str | None = None, company_name: str | None = None) -> Iterable[LicenseEntitlement]:
        ...


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(normalized)


class SoloCsvActivationSource:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch_activations(self, license_id: str | None = None, company_name: str | None = None) -> Iterable[Activation]:
        with self.path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                row_license_id = row.get("License ID") or row.get("license_id") or ""
                row_company = row.get("Company Name") or row.get("company_name") or ""
                if license_id and row_license_id != license_id:
                    continue
                if company_name and company_name.lower() not in row_company.lower():
                    continue
                activation_date = parse_datetime(row.get("Activation Date") or row.get("activation_date"))
                if not activation_date:
                    continue
                yield Activation(
                    license_id=row_license_id,
                    company_name=row_company,
                    activation_date=activation_date,
                    license_entered_date=parse_datetime(row.get("License Entered Date")),
                    status=row.get("Status"),
                    ip_address=row.get("IP Address") or row.get("ip_address"),
                    initial_product_version=row.get("Initial Product Version"),
                    deactivated_date=parse_datetime(row.get("Deactivated Date")),
                )


class AwsUsageCsvSource:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch_usage(self, license_id: str | None = None, company_name: str | None = None) -> Iterable[UsageRecord]:
        with self.path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                row_license_id = row.get("License ID") or row.get("license_id") or ""
                row_company = row.get("Company Name") or row.get("company_name") or ""
                if license_id and row_license_id != license_id:
                    continue
                if company_name and company_name.lower() not in row_company.lower():
                    continue
                start_time = parse_datetime(row.get("Start Time") or row.get("start_time"))
                if not start_time:
                    continue
                yield UsageRecord(
                    license_id=row_license_id,
                    company_name=row_company,
                    start_time=start_time,
                    end_time=parse_datetime(row.get("End Time") or row.get("end_time")),
                    links_processed=int(row.get("Links Processed") or row.get("links_processed") or 0),
                    files_processed=int(row.get("Files Processed") or row.get("files_processed") or 0),
                    file_size_bytes=int(row.get("File Size in Bytes") or row.get("file_size_bytes") or 0),
                    process_name=row.get("Process Name"),
                    machine_name=row.get("Machine Name"),
                    username=row.get("Username"),
                    mac_address=row.get("MAC address") or row.get("mac_address"),
                    ip_address=row.get("IP Address") or row.get("ip_address"),
                    tenant_name=row.get("Tenant Name"),
                    site_name=row.get("Site Name"),
                    tenant_id=row.get("Tenant ID"),
                    database_name=row.get("Database Name"),
                )


class ZohoCsvEntitlementSource:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch_entitlements(self, license_id: str | None = None, company_name: str | None = None) -> Iterable[LicenseEntitlement]:
        with self.path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                row_license_id = row.get("License ID") or row.get("license_id") or ""
                row_company = row.get("Company Name") or row.get("company_name") or ""
                if license_id and row_license_id != license_id:
                    continue
                if company_name and company_name.lower() not in row_company.lower():
                    continue
                allowed_countries = _split_set(row.get("Allowed Countries"))
                allowed_states = _split_set(row.get("Allowed States"))
                allowed_cities = _split_set(row.get("Allowed Cities"))
                org_definition = OrganizationDefinition(
                    company_name=row_company,
                    allowed_countries=allowed_countries,
                    allowed_states=allowed_states,
                    allowed_cities=allowed_cities,
                    notes=row.get("Organization Definition Notes"),
                )
                yield LicenseEntitlement(
                    license_id=row_license_id,
                    company_name=row_company,
                    personnel_licensed=int(row.get("Personnel Licensed") or row.get("personnel_licensed") or 0),
                    organization_definition=org_definition,
                )


def _split_set(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(part.strip() for part in value.split(";") if part.strip())

