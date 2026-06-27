import json
import tempfile
from pathlib import Path
from unittest import TestCase

from license_agent.data_query import (
    DataQueryService,
    extract_company_name,
    extract_license_text,
    extract_usage_company_name,
    looks_like_data_query,
)
from license_agent.settings import LicenseAgentSettings


class FakeAuroraClient:
    def __init__(self) -> None:
        self.active_license_calls = []
        self.linked_record_calls = []

    def status(self) -> dict:
        return {"configured": True}

    def search_active_linktek_licenses(self, *, company_name=None, license_text=None, limit=10) -> dict:
        self.active_license_calls.append(
            {"company_name": company_name, "license_text": license_text, "limit": limit}
        )
        return {
            "configured": True,
            "rows": [
                {
                    "id": "crm-license-1",
                    "license_code": "LTK-1234",
                    "company": "Example Corp",
                    "product": "LinkTek",
                    "maintenance_expiry_date": "2027-01-01",
                }
            ],
            "error": "",
        }

    def linked_records_for_active_licenses(self, license_rows, *, per_table_limit=5) -> dict:
        self.linked_record_calls.append({"license_rows": license_rows, "per_table_limit": per_table_limit})
        return {
            "configured": True,
            "licenses": [
                {
                    "license": license_rows[0],
                    "linked_records": {
                        "sales_routing_forms": [{"Name": "SRF-1"}],
                        "license_verifications": [{"Name": "LV-1"}],
                        "quote_line_item_sets": [],
                    },
                }
            ],
            "error": "",
        }


class FakeUsageClient:
    def __init__(self, match=None, error: str = "") -> None:
        self.match = match
        self.error = error
        self.company_calls = []

    def status(self) -> dict:
        return {"configured": True}

    def find_company(self, company_name: str) -> dict:
        self.company_calls.append(company_name)
        return {"configured": True, "error": self.error, "match": self.match}


class DataQueryServiceTests(TestCase):
    def test_answers_signal_summary_from_latest_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            analysis_root = Path(temp_dir) / "analysis"
            report_dir = analysis_root / "20260624T154914Z"
            report_dir.mkdir(parents=True)
            (report_dir / "cohort_report.json").write_text(
                json.dumps(
                    {
                        "solo_export_license_count": 3845,
                        "solo_activation_license_count": 4380,
                        "solo_activation_company_count": 2484,
                        "solo_activation_path_count": 18,
                        "license_verification_overlap_count": 27,
                        "broad_srf_overlap_count": 8,
                        "general_population_summary": {
                            "share_with_rejections": 0.2,
                            "share_with_multi_ip": 0.4,
                            "share_with_multi_installation": 0.5,
                        },
                        "license_verification_overlap_summary": {
                            "share_with_rejections": 0.5,
                            "share_with_multi_ip": 0.8,
                            "share_with_multi_installation": 0.9,
                        },
                        "outpoints": ["Rejected activations are materially more common."],
                    }
                ),
                encoding="utf-8",
            )
            service = DataQueryService(LicenseAgentSettings(), analysis_root=analysis_root)
            result = service.answer("What are the strongest violation signals?")

        self.assertEqual(result.kind, "signal_summary")
        self.assertIn("rejected-activation", result.message)
        self.assertIn("50.0%", result.message)

    def test_answers_company_lookup_from_overlap_and_activation_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            analysis_root = Path(temp_dir) / "analysis"
            activation_root = Path(temp_dir) / "activations"
            report_dir = analysis_root / "20260624T154914Z"
            report_dir.mkdir(parents=True)
            activation_root.mkdir(parents=True)
            (report_dir / "cohort_report.json").write_text(
                json.dumps(
                    {
                        "license_verification_overlap_companies": [
                            {"company_key": "example", "company_name": "Example Corp"}
                        ],
                        "broad_srf_overlap_companies": [],
                    }
                ),
                encoding="utf-8",
            )
            (activation_root / "activation_data.csv").write_text(
                "\n".join(
                    [
                        "CompanyName,ActivationDate,Status,IPAddress,InstallationID,ComputerID,DeactivatedDate,LicenseID",
                        "Example Corp,2025-01-01T00:00:00,Rejected,1.1.1.1,A,100,,123",
                        "Example Corp,2025-01-02T00:00:00,Successful,2.2.2.2,B,101,2025-01-03T00:00:00,123",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            service = DataQueryService(
                LicenseAgentSettings(),
                analysis_root=analysis_root,
                solo_activation_root=activation_root,
            )
            result = service.answer("Is Example Corp in the violator overlap?")

        self.assertEqual(result.kind, "company_signal_lookup")
        self.assertIn("License Verification violator overlap", result.message)
        self.assertIn("2 activation", result.message)

    def test_company_lookup_uses_curated_solo_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            analysis_root = Path(temp_dir) / "analysis"
            report_dir = analysis_root / "20260624T154914Z"
            report_dir.mkdir(parents=True)
            summary_path = Path(temp_dir) / "solo_summary.json"
            (report_dir / "cohort_report.json").write_text(
                json.dumps({"license_verification_overlap_companies": [], "broad_srf_overlap_companies": []}),
                encoding="utf-8",
            )
            summary_path.write_text(
                json.dumps(
                    {
                        "companies": {
                            "example": {
                                "company_key": "example",
                                "company_name": "Example Corp",
                                "activations": 3,
                                "successful_activations": 2,
                                "rejected_activations": 1,
                                "rejection_rate": 1 / 3,
                                "unique_ips": 2,
                                "unique_installations": 2,
                                "unique_computers": 1,
                                "deactivations": 0,
                                "unique_licenses": 1,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            service = DataQueryService(
                LicenseAgentSettings(solo_summary_local_path=str(summary_path)),
                analysis_root=analysis_root,
                solo_activation_root=Path(temp_dir) / "missing",
            )
            result = service.answer("Is Example Corp in the violator overlap?")

        self.assertEqual(result.kind, "company_signal_lookup")
        self.assertIn("3 activation", result.message)
        self.assertIn("1 rejection", result.message)

    def test_crm_lookup_reports_when_aurora_unconfigured(self) -> None:
        service = DataQueryService(LicenseAgentSettings())
        result = service.answer("Look up Hudson Housing Capital LLC in CRM")

        self.assertEqual(result.kind, "crm_lookup_unconfigured")
        self.assertIn("Aurora is not configured", result.message)

    def test_active_linktek_license_query_uses_constrained_aurora_lookup(self) -> None:
        aurora_client = FakeAuroraClient()
        service = DataQueryService(LicenseAgentSettings(), aurora_client=aurora_client)
        result = service.answer("Show active LinkTek licenses for Example Corp")

        self.assertEqual(result.kind, "active_linktek_licenses")
        self.assertIn("Example Corp / LTK-1234", result.message)
        self.assertEqual(aurora_client.active_license_calls[0]["company_name"], "Example Corp")
        self.assertIsNone(aurora_client.active_license_calls[0]["license_text"])

    def test_linked_license_record_query_fetches_active_license_first(self) -> None:
        aurora_client = FakeAuroraClient()
        service = DataQueryService(LicenseAgentSettings(), aurora_client=aurora_client)
        result = service.answer("Show linked records for active LinkTek licenses for Example Corp")

        self.assertEqual(result.kind, "linked_records")
        self.assertIn("1 active LinkTek license", result.message)
        self.assertIn("2 linked CRM record", result.message)
        self.assertEqual(aurora_client.active_license_calls[0]["company_name"], "Example Corp")
        self.assertEqual(len(aurora_client.linked_record_calls[0]["license_rows"]), 1)

    def test_extract_company_name_from_chatty_question(self) -> None:
        self.assertEqual(
            extract_company_name("Is Hudson Housing Capital LLC in the violator overlap?"),
            "Hudson Housing Capital LLC",
        )
        self.assertEqual(
            extract_company_name("Show linked records for active LinkTek licenses for Example Corp"),
            "Example Corp",
        )
        self.assertEqual(
            extract_company_name("Hi, do you have data on how many files Mediterranean Shipping Company actually ran?"),
            "Mediterranean Shipping Company",
        )

    def test_usage_activity_query_reports_from_usage_summary(self) -> None:
        usage_client = FakeUsageClient(
            {
                "company_name": "Mediterranean Shipping Company",
                "files_processed": 12345,
                "links_processed": 67890,
                "file_size_gib": 12.5,
                "run_count": 44,
                "license_ids": ["66000000"],
                "machine_count": 2,
                "mac_count": 3,
                "first_start_time": "2024-01-01 10:00:00",
                "last_end_time": "2025-01-01 11:00:00",
                "tasks": ["Inoculate", "Cure"],
            }
        )
        service = DataQueryService(LicenseAgentSettings(), usage_client=usage_client)
        result = service.answer("Hi, do you have data on how many files Mediterranean Shipping Company actually ran?")

        self.assertEqual(result.kind, "usage_activity")
        self.assertIn("Mediterranean Shipping Company", result.message)
        self.assertIn("12,345 file", result.message)
        self.assertEqual(usage_client.company_calls, ["Mediterranean Shipping Company"])

    def test_usage_activity_query_reports_missing_summary(self) -> None:
        service = DataQueryService(LicenseAgentSettings())
        result = service.answer("Hi, do you have data on how many files Mediterranean Shipping Company actually ran?")

        self.assertEqual(result.kind, "usage_activity_unavailable")
        self.assertIn("Mediterranean Shipping Company", result.message)
        self.assertIn("ProcessInfo usage summary has not been generated", result.message)

    def test_extract_usage_company_name(self) -> None:
        self.assertEqual(
            extract_usage_company_name("How many files Mediterranean Shipping Company actually ran?"),
            "Mediterranean Shipping Company",
        )

    def test_extract_license_text_from_chatty_question(self) -> None:
        self.assertEqual(extract_license_text("Show records linked to license id LTK-1234"), "LTK-1234")

    def test_detects_data_query(self) -> None:
        self.assertTrue(looks_like_data_query("What are the strongest violation signals?"))
        self.assertTrue(looks_like_data_query("Look up Hudson Housing in CRM"))
        self.assertTrue(looks_like_data_query("Show active LinkTek licenses for Example Corp"))
        self.assertTrue(looks_like_data_query("Show records linked to license id LTK-1234"))
        self.assertTrue(looks_like_data_query("How many files Mediterranean Shipping Company actually ran?"))
