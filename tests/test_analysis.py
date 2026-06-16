from datetime import datetime
from unittest import TestCase

from license_agent import (
    Activation,
    GeoLocation,
    InvestigationInput,
    LicenseEntitlement,
    LicenseViolationAgent,
    OrganizationDefinition,
    UsageRecord,
)


class AnalysisTests(TestCase):
    def test_report_flags_location_delay_and_usage_volume(self) -> None:
        activation = Activation(
            license_id="LIC-123",
            company_name="Example Corp",
            activation_date=datetime(2025, 1, 1),
            ip_address="203.0.113.10",
        )
        usage = UsageRecord(
            license_id="LIC-123",
            company_name="Different Holdings",
            start_time=datetime(2025, 5, 1),
            file_size_bytes=260 * 1024**3,
            links_processed=10,
            files_processed=20,
            machine_name="VM-PROD-01",
            ip_address="198.51.100.10",
        )
        entitlement = LicenseEntitlement(
            license_id="LIC-123",
            company_name="Example Corp",
            personnel_licensed=2,
            organization_definition=OrganizationDefinition(
                company_name="Example Corp",
                allowed_countries=frozenset({"United States"}),
                allowed_states=frozenset({"Ohio"}),
            ),
        )
        locations = (
            GeoLocation(ip_address="203.0.113.10", city="Columbus", state="Ohio", country="United States"),
            GeoLocation(ip_address="198.51.100.10", city="Toronto", state="Ontario", country="Canada"),
        )

        report = LicenseViolationAgent().create_report(
            InvestigationInput(
                license_id="LIC-123",
                activations=(activation,),
                usage_records=(usage,),
                entitlements=(entitlement,),
                geolocations=locations,
            )
        )

        codes = {finding.code for finding in report.findings}
        self.assertIn("location_outside_org_definition", codes)
        self.assertIn("long_activation_to_usage_delay", codes)
        self.assertIn("usage_over_eula_review_threshold", codes)
        self.assertIn("company_name_mismatch", codes)
        self.assertEqual(report.total_file_size_gb, 260)
        self.assertIn("Potential violation indicators", report.evaluation)

    def test_report_passes_when_data_is_consistent(self) -> None:
        activation = Activation(
            license_id="LIC-456",
            company_name="Consistent Inc",
            activation_date=datetime(2025, 1, 1),
            ip_address="203.0.113.20",
        )
        usage = UsageRecord(
            license_id="LIC-456",
            company_name="Consistent Inc",
            start_time=datetime(2025, 1, 10),
            file_size_bytes=50 * 1024**3,
            machine_name="FORENSICS-01",
            username="analyst",
            ip_address="203.0.113.20",
        )
        entitlement = LicenseEntitlement(
            license_id="LIC-456",
            company_name="Consistent Inc",
            personnel_licensed=2,
            organization_definition=OrganizationDefinition(
                company_name="Consistent Inc",
                allowed_countries=frozenset({"United States"}),
                allowed_states=frozenset({"Ohio"}),
            ),
        )
        locations = (
            GeoLocation(ip_address="203.0.113.20", city="Columbus", state="Ohio", country="United States"),
        )

        report = LicenseViolationAgent().create_report(
            InvestigationInput(
                license_id="LIC-456",
                activations=(activation,),
                usage_records=(usage,),
                entitlements=(entitlement,),
                geolocations=locations,
            )
        )

        self.assertEqual(report.findings, ())
        self.assertIn("No material violation indicators", report.evaluation)
