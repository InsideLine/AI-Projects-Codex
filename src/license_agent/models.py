from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class Activation:
    license_id: str
    company_name: str
    activation_date: datetime
    license_entered_date: datetime | None = None
    status: str | None = None
    ip_address: str | None = None
    initial_product_version: str | None = None
    deactivated_date: datetime | None = None


@dataclass(frozen=True)
class UsageRecord:
    license_id: str
    company_name: str
    start_time: datetime
    end_time: datetime | None = None
    links_processed: int = 0
    files_processed: int = 0
    file_size_bytes: int = 0
    process_name: str | None = None
    machine_name: str | None = None
    username: str | None = None
    mac_address: str | None = None
    ip_address: str | None = None
    tenant_name: str | None = None
    site_name: str | None = None
    tenant_id: str | None = None
    database_name: str | None = None


@dataclass(frozen=True)
class GeoLocation:
    ip_address: str
    city: str | None = None
    state: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    accuracy_radius_km: float | None = None
    provider: str | None = None
    confidence: float | None = None
    looked_up_at: datetime | None = None


@dataclass(frozen=True)
class OrganizationDefinition:
    company_name: str
    allowed_countries: frozenset[str] = field(default_factory=frozenset)
    allowed_states: frozenset[str] = field(default_factory=frozenset)
    allowed_cities: frozenset[str] = field(default_factory=frozenset)
    notes: str | None = None

    def allows(self, location: GeoLocation) -> bool:
        if self.allowed_countries and location.country not in self.allowed_countries:
            return False
        if self.allowed_states and location.state not in self.allowed_states:
            return False
        if self.allowed_cities and location.city not in self.allowed_cities:
            return False
        return True


@dataclass(frozen=True)
class LicenseEntitlement:
    license_id: str
    company_name: str
    personnel_licensed: int
    eula_usage_threshold_gb_per_person: float = 100.0
    organization_definition: OrganizationDefinition | None = None


@dataclass(frozen=True)
class InvestigationInput:
    license_id: str | None = None
    company_name: str | None = None
    activations: tuple[Activation, ...] = ()
    usage_records: tuple[UsageRecord, ...] = ()
    entitlements: tuple[LicenseEntitlement, ...] = ()
    geolocations: tuple[GeoLocation, ...] = ()


@dataclass(frozen=True)
class Finding:
    code: str
    title: str
    severity: Severity
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InvestigationReport:
    subject: str
    created_at: datetime
    findings: tuple[Finding, ...]
    activation_count: int
    usage_record_count: int
    total_links_processed: int
    total_files_processed: int
    total_file_size_bytes: int
    evaluation: str

    @property
    def total_file_size_gb(self) -> float:
        return self.total_file_size_bytes / (1024**3)


@dataclass(frozen=True)
class FeedbackEvent:
    report_subject: str
    finding_code: str
    accepted: bool
    analyst: str
    comment: str
    created_at: datetime

