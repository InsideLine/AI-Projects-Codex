from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher

from .models import (
    Activation,
    Finding,
    GeoLocation,
    InvestigationInput,
    InvestigationReport,
    LicenseEntitlement,
    Severity,
    UsageRecord,
)


class RuleEngine:
    """Deterministic checks mapped to the investigation checklist."""

    def __init__(self, usage_delay_months: int = 3) -> None:
        self.usage_delay_days = usage_delay_months * 30

    def analyze(self, investigation: InvestigationInput) -> InvestigationReport:
        activations = sorted(investigation.activations, key=lambda row: row.activation_date)
        usage_records = sorted(investigation.usage_records, key=lambda row: row.start_time)
        geos_by_ip = {geo.ip_address: geo for geo in investigation.geolocations}
        entitlements_by_license = {item.license_id: item for item in investigation.entitlements}

        findings: list[Finding] = []
        findings.extend(self._location_findings(activations, usage_records, geos_by_ip, entitlements_by_license))
        findings.extend(self._timeline_findings(activations, usage_records))
        findings.extend(self._usage_volume_findings(usage_records, entitlements_by_license))
        findings.extend(self._logical_consistency_findings(activations, usage_records))

        total_links = sum(max(record.links_processed, 0) for record in usage_records)
        total_files = sum(max(record.files_processed, 0) for record in usage_records)
        total_bytes = sum(max(record.file_size_bytes, 0) for record in usage_records)

        subject = investigation.license_id or investigation.company_name or "unknown subject"
        return InvestigationReport(
            subject=subject,
            created_at=datetime.utcnow(),
            findings=tuple(findings),
            activation_count=len(activations),
            usage_record_count=len(usage_records),
            total_links_processed=total_links,
            total_files_processed=total_files,
            total_file_size_bytes=total_bytes,
            evaluation=self._evaluation(findings),
        )

    def _location_findings(
        self,
        activations: list[Activation],
        usage_records: list[UsageRecord],
        geos_by_ip: dict[str, GeoLocation],
        entitlements_by_license: dict[str, LicenseEntitlement],
    ) -> list[Finding]:
        findings: list[Finding] = []
        ip_rows: list[tuple[str, str, str]] = []

        for activation in activations:
            if activation.ip_address:
                ip_rows.append((activation.license_id, activation.ip_address, "activation"))
        for record in usage_records:
            if record.ip_address:
                ip_rows.append((record.license_id, record.ip_address, "usage"))

        for license_id, ip_address, source in ip_rows:
            geo = geos_by_ip.get(ip_address)
            if not geo:
                findings.append(
                    Finding(
                        code="missing_ip_geolocation",
                        title="IP address lacks geolocation enrichment",
                        severity=Severity.LOW,
                        detail=f"{source.title()} IP {ip_address} has no city/state/country lookup.",
                        evidence={"license_id": license_id, "ip_address": ip_address, "source": source},
                    )
                )
                continue

            entitlement = entitlements_by_license.get(license_id)
            org_definition = entitlement.organization_definition if entitlement else None
            if org_definition and not org_definition.allows(geo):
                findings.append(
                    Finding(
                        code="location_outside_org_definition",
                        title="Location outside organization definition",
                        severity=Severity.HIGH,
                        detail=(
                            f"{source.title()} IP {ip_address} resolved to "
                            f"{geo.city or 'unknown city'}, {geo.state or 'unknown state'}, "
                            f"{geo.country or 'unknown country'}, which is outside the licensed definition."
                        ),
                        evidence={
                            "license_id": license_id,
                            "ip_address": ip_address,
                            "source": source,
                            "city": geo.city,
                            "state": geo.state,
                            "country": geo.country,
                            "provider": geo.provider,
                            "accuracy_radius_km": geo.accuracy_radius_km,
                        },
                    )
                )

        return findings

    def _timeline_findings(
        self,
        activations: list[Activation],
        usage_records: list[UsageRecord],
    ) -> list[Finding]:
        findings: list[Finding] = []
        usage_by_license: dict[str, list[UsageRecord]] = defaultdict(list)
        for record in usage_records:
            usage_by_license[record.license_id].append(record)

        for activation in activations:
            matching_usage = [row for row in usage_by_license.get(activation.license_id, []) if row.start_time >= activation.activation_date]
            if not matching_usage:
                findings.append(
                    Finding(
                        code="activation_without_later_usage",
                        title="Activation has no later usage record",
                        severity=Severity.MEDIUM,
                        detail="No usage record was found after this activation date.",
                        evidence={"license_id": activation.license_id, "activation_date": activation.activation_date.isoformat()},
                    )
                )
                continue

            first_usage = matching_usage[0]
            delay_days = (first_usage.start_time - activation.activation_date).days
            if delay_days >= self.usage_delay_days:
                findings.append(
                    Finding(
                        code="long_activation_to_usage_delay",
                        title="Long delay between activation and first usage",
                        severity=Severity.MEDIUM,
                        detail=(
                            f"The first logical usage record is {delay_days} days after activation. "
                            "This could indicate missing usage data for the intervening period."
                        ),
                        evidence={
                            "license_id": activation.license_id,
                            "activation_date": activation.activation_date.isoformat(),
                            "first_usage_date": first_usage.start_time.isoformat(),
                            "machine_name": first_usage.machine_name,
                            "usage_ip_address": first_usage.ip_address,
                        },
                    )
                )

        return findings

    def _usage_volume_findings(
        self,
        usage_records: list[UsageRecord],
        entitlements_by_license: dict[str, LicenseEntitlement],
    ) -> list[Finding]:
        findings: list[Finding] = []
        bytes_by_license: dict[str, int] = defaultdict(int)
        links_by_license: dict[str, int] = defaultdict(int)
        files_by_license: dict[str, int] = defaultdict(int)

        for record in usage_records:
            bytes_by_license[record.license_id] += max(record.file_size_bytes, 0)
            links_by_license[record.license_id] += max(record.links_processed, 0)
            files_by_license[record.license_id] += max(record.files_processed, 0)

        for license_id, total_bytes in bytes_by_license.items():
            entitlement = entitlements_by_license.get(license_id)
            if not entitlement or entitlement.personnel_licensed <= 0:
                findings.append(
                    Finding(
                        code="missing_entitlement",
                        title="Missing license entitlement data",
                        severity=Severity.MEDIUM,
                        detail="Usage volume cannot be evaluated because personnel licensed is missing.",
                        evidence={"license_id": license_id},
                    )
                )
                continue

            total_gb = total_bytes / (1024**3)
            gb_per_person = total_gb / entitlement.personnel_licensed
            threshold = entitlement.eula_usage_threshold_gb_per_person
            if gb_per_person > threshold:
                findings.append(
                    Finding(
                        code="usage_over_eula_review_threshold",
                        title="Usage exceeds EULA review threshold",
                        severity=Severity.HIGH,
                        detail=(
                            f"Usage is {gb_per_person:.1f} GB per licensed personnel, above the "
                            f"{threshold:.1f} GB review threshold. This is a suspicion signal, not proof."
                        ),
                        evidence={
                            "license_id": license_id,
                            "personnel_licensed": entitlement.personnel_licensed,
                            "total_gb": round(total_gb, 2),
                            "gb_per_person": round(gb_per_person, 2),
                            "links_processed": links_by_license[license_id],
                            "files_processed": files_by_license[license_id],
                        },
                    )
                )

        return findings

    def _logical_consistency_findings(
        self,
        activations: list[Activation],
        usage_records: list[UsageRecord],
    ) -> list[Finding]:
        findings: list[Finding] = []
        activation_company_by_license = {
            row.license_id: row.company_name for row in activations if row.company_name
        }

        for record in usage_records:
            activation_company = activation_company_by_license.get(record.license_id)
            if activation_company and record.company_name:
                similarity = SequenceMatcher(None, activation_company.lower(), record.company_name.lower()).ratio()
                if similarity < 0.72:
                    findings.append(
                        Finding(
                            code="company_name_mismatch",
                            title="Company name differs across activation and usage data",
                            severity=Severity.MEDIUM,
                            detail=(
                                f"Activation company '{activation_company}' differs from usage company "
                                f"'{record.company_name}'."
                            ),
                            evidence={
                                "license_id": record.license_id,
                                "activation_company": activation_company,
                                "usage_company": record.company_name,
                                "similarity": round(similarity, 3),
                            },
                        )
                    )

            if not record.machine_name and not record.username and not record.tenant_name:
                findings.append(
                    Finding(
                        code="sparse_usage_identity",
                        title="Usage record lacks identifying fields",
                        severity=Severity.LOW,
                        detail="Usage record has no machine name, username, or tenant name.",
                        evidence={"license_id": record.license_id, "start_time": record.start_time.isoformat()},
                    )
                )

        return findings

    def _evaluation(self, findings: list[Finding]) -> str:
        high_count = sum(1 for finding in findings if finding.severity == Severity.HIGH)
        medium_count = sum(1 for finding in findings if finding.severity == Severity.MEDIUM)

        if high_count:
            return "Potential violation indicators found. Human review is recommended before any customer-facing action."
        if medium_count:
            return "Some inconsistencies or missing evidence found. Additional data gathering is recommended."
        return "No material violation indicators were found from the supplied data."

