from unittest import TestCase

from license_agent.correlation_analysis import (
    classify_verification,
    derive_company_name_from_site_row,
    normalize_company_name,
    parse_zoho_datetime,
    parse_sales_routing_violations,
    parse_sites,
)


class CorrelationAnalysisTests(TestCase):
    def test_normalize_company_name_strips_basic_suffixes(self) -> None:
        self.assertEqual(normalize_company_name("The Blackstone Group, Inc."), "blackstone group")
        self.assertEqual(normalize_company_name("Volkert, Inc."), "volkert")
        self.assertEqual(normalize_company_name("Colliers International (WA) Pty Ltd"), "colliers international wa")

    def test_classify_verification_marks_downstream_stages_as_violation(self) -> None:
        self.assertEqual(classify_verification(stage="Paid", current_status=""), "violation")
        self.assertEqual(classify_verification(stage="With VPS", current_status="Waiting on invoice"), "violation")

    def test_classify_verification_marks_not_violation_status_text(self) -> None:
        self.assertEqual(
            classify_verification(
                stage="Initial Inspection",
                current_status="11 Mar - Not a LV. Org def is being updated.",
            ),
            "not_violation",
        )

    def test_classify_verification_keeps_open_work_as_in_review(self) -> None:
        self.assertEqual(
            classify_verification(
                stage="Data Gathering",
                current_status="Waiting for more information from the rep.",
            ),
            "in_review",
        )

    def test_derive_company_name_from_site_row_falls_back_to_site_prefix(self) -> None:
        self.assertEqual(
            derive_company_name_from_site_row({"Company Name": "", "Site Name": "Johnson & Johnson | Skillman"}),
            "Johnson & Johnson",
        )

    def test_parse_sales_routing_violations_uses_yes_flag_and_site_mapping(self) -> None:
        sites_csv = "Id,Site Name,Company Name\n1,Johnson & Johnson | Skillman,\n2,Aecon | Toronto,Aecon\n"
        sales_csv = (
            "Id,Purchasing Site,License ID,License Violation,Created Time,Modified Time\n"
            "10,1,123,Yes,\"Jan 01, 2026 01:00 PM\",2026-01-01\n"
            "11,2,456,No,\"Jan 02, 2026 01:00 PM\",2026-01-02\n"
        )
        site_map = parse_sites(sites_csv)
        rows = parse_sales_routing_violations(sales_csv, site_map)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].company_name, "Johnson & Johnson")
        self.assertEqual(rows[0].license_id, "123")

    def test_parse_sales_routing_violations_applies_created_time_cutoff(self) -> None:
        sites_csv = "Id,Site Name,Company Name\n1,Johnson & Johnson | Skillman,\n"
        sales_csv = (
            "Id,Purchasing Site,License ID,License Violation,Created Time,Modified Time\n"
            "10,1,123,Yes,\"Jan 01, 2022 01:00 PM\",2026-01-01\n"
            "11,1,456,Yes,\"Jan 01, 2026 01:00 PM\",2026-01-02\n"
        )
        site_map = parse_sites(sites_csv)
        rows = parse_sales_routing_violations(
            sales_csv,
            site_map,
            min_created_time=parse_zoho_datetime("Jun 19, 2023 12:00 AM"),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].license_id, "456")
