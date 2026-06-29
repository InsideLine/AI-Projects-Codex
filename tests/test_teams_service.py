import tempfile
from pathlib import Path
from unittest import TestCase

from license_agent.data_query import DataQueryResult
from license_agent.settings import LicenseAgentSettings
from license_agent.teams_service import TeamsChatService, build_usage_report_result, parse_intent


class FakeUsageClient:
    def __init__(self, match=None, candidates=None) -> None:
        self.match = match
        self.candidates = candidates or []

    def find_company(self, company_name: str) -> dict:
        return {
            "configured": True,
            "error": "",
            "match": self.match,
            "candidates": self.candidates,
            "summary_meta": {"company_count": 1},
        }

    def find_license(self, license_id: str) -> dict:
        return {"configured": True, "error": "", "match": self.match, "summary_meta": {"company_count": 1}}


class FakeReportQueryService:
    def __init__(self, match=None, ip_records=None) -> None:
        self.usage_client = FakeUsageClient(match)
        self.ip_geolocation_client = FakeIpGeolocationClient(ip_records or {})

    def runtime_status(self):
        return {"aws_usage_summary": {"configured": bool(self.usage_client.match)}}

    def answer(self, text: str) -> DataQueryResult:
        return DataQueryResult(kind="fake", message=text, evidence={})


class FakeIpGeolocationClient:
    def __init__(self, records) -> None:
        self.records = records

    def status(self):
        return {"configured": bool(self.records)}

    def lookup_many(self, ip_addresses):
        return {
            "configured": bool(self.records),
            "error": "",
            "records": {
                ip_address: self.records[ip_address]
                for ip_address in ip_addresses
                if ip_address in self.records
            },
            "meta": {"provider": "maxmind_geolite2_city"},
        }


class TeamsIntentTests(TestCase):
    def test_parses_feedback_command(self) -> None:
        intent = parse_intent("feedback usage_over_eula_review_threshold accepted Matched analyst review")
        self.assertEqual(intent.kind, "feedback")
        self.assertEqual(intent.finding_code, "usage_over_eula_review_threshold")
        self.assertTrue(intent.accepted)
        self.assertEqual(intent.comment, "Matched analyst review")

    def test_parses_license_request(self) -> None:
        intent = parse_intent("license 66275132")
        self.assertEqual(intent.kind, "report_request")
        self.assertEqual(intent.subject_type, "license")
        self.assertEqual(intent.subject_value, "66275132")

    def test_parses_natural_language_company_request(self) -> None:
        intent = parse_intent("Can you check whether Hudson Housing Capital LLC might be violating their license?")
        self.assertEqual(intent.kind, "report_request")
        self.assertEqual(intent.subject_type, "company")
        self.assertEqual(intent.subject_value, "Hudson Housing Capital LLC")

    def test_parses_full_report_request_without_trailing_instructions(self) -> None:
        intent = parse_intent(
            "Can you give me a full report on Mediterranean Shipping Company Pty Ltd. "
            "I am looking for possible evidence that they might be violation the LinkTek EULA (license violation)."
        )
        self.assertEqual(intent.kind, "report_request")
        self.assertEqual(intent.subject_type, "company")
        self.assertEqual(intent.subject_value, "Mediterranean Shipping Company Pty Ltd")

    def test_parses_explanation_request(self) -> None:
        intent = parse_intent("Why did you flag this as suspicious?")
        self.assertEqual(intent.kind, "explain_last_report")

    def test_parses_natural_feedback_when_single_finding_context_exists(self) -> None:
        last_job = {
            "result": {
                "findings": [
                    {"code": "company_name_mismatch", "title": "Mismatch"},
                ]
            }
        }
        intent = parse_intent("That finding is wrong because this subsidiary is covered.", last_job=last_job)
        self.assertEqual(intent.kind, "feedback")
        self.assertEqual(intent.finding_code, "company_name_mismatch")
        self.assertFalse(intent.accepted)

    def test_parses_follow_up_question(self) -> None:
        intent = parse_intent("Have we seen this company using the same license across multiple machines?")
        self.assertEqual(intent.kind, "follow_up_question")

    def test_parses_data_query(self) -> None:
        intent = parse_intent("What are the strongest violation signals?")
        self.assertEqual(intent.kind, "data_query")

    def test_parses_usage_question_as_data_query_not_company_report(self) -> None:
        intent = parse_intent("Hi, do you have data on how many files Mediterranean Shipping Company actually ran?")
        self.assertEqual(intent.kind, "data_query")

    def test_parses_company_history_question(self) -> None:
        intent = parse_intent("What are the companies that I have asked about so far?")
        self.assertEqual(intent.kind, "company_history")


class TeamsChatServiceTests(TestCase):
    def test_sync_report_job_completes_and_is_visible_in_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = LicenseAgentSettings(
                app_db_path=str(Path(temp_dir) / "app.sqlite3"),
                report_output_root=str(Path(temp_dir) / "reports"),
            )
            service = TeamsChatService(settings, run_async=False)
            response = service.handle_message("license 66275132", "analyst@example.com")
            self.assertEqual(response["type"], "report_requested")
            job_id = response["job_id"]
            job = service.get_job(job_id)
            assert job is not None
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["subject_type"], "license")
            history = service.handle_message("history", "analyst@example.com")
            self.assertIn("license `66275132`", history["message"])
            self.assertNotIn(job_id, history["message"])

    def test_company_history_question_lists_prior_companies_without_creating_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = LicenseAgentSettings(
                app_db_path=str(Path(temp_dir) / "app.sqlite3"),
                report_output_root=str(Path(temp_dir) / "reports"),
            )
            service = TeamsChatService(settings, run_async=False)
            service.handle_message("company Example Corp", "analyst@example.com")
            service.handle_message("company Rockwool Bv", "analyst@example.com")

            response = service.handle_message(
                "What are the companies that I have asked about so far?",
                "analyst@example.com",
            )

            self.assertEqual(response["type"], "company_history")
            self.assertIn("`Example Corp`", response["message"])
            self.assertIn("`Rockwool Bv`", response["message"])
            self.assertNotIn("I'm working on the report", response["message"])
            self.assertNotIn("I completed the report", response["message"])
            self.assertEqual(len(service.store.recent_jobs("analyst@example.com")), 2)

    def test_feedback_records_preference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = LicenseAgentSettings(
                app_db_path=str(Path(temp_dir) / "app.sqlite3"),
                report_output_root=str(Path(temp_dir) / "reports"),
            )
            service = TeamsChatService(settings, run_async=False)
            service.handle_message("company Example Corp", "analyst@example.com")
            response = service.handle_message(
                "feedback company_name_mismatch wrong Covered by contract amendment",
                "analyst@example.com",
            )
            self.assertEqual(response["type"], "feedback_recorded")
            state = service.state("analyst@example.com")
            preferences = state["memory"]["preferences"]
            self.assertTrue(preferences)
            self.assertEqual(preferences[0]["preference_key"], "rejected_finding_feedback")

    def test_explanation_uses_latest_report_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = LicenseAgentSettings(
                app_db_path=str(Path(temp_dir) / "app.sqlite3"),
                report_output_root=str(Path(temp_dir) / "reports"),
            )
            service = TeamsChatService(settings, run_async=False)
            job = service.store.create_job(
                user_id="analyst@example.com",
                job_type="investigation_report",
                subject_type="license",
                subject_value="66275132",
                payload={"subject_type": "license", "subject_value": "66275132"},
            )
            service.store.mark_completed(
                job["job_id"],
                {
                    "subject": "66275132",
                    "evaluation": "review recommended",
                    "finding_count": 1,
                    "findings": [
                        {
                            "code": "usage_over_eula_review_threshold",
                            "title": "Usage exceeds EULA review threshold",
                        }
                    ],
                },
            )
            response = service.handle_message("Why did you flag this as suspicious?", "analyst@example.com")
            self.assertEqual(response["type"], "explanation")
            self.assertIn("usage_over_eula_review_threshold", response["message"])

    def test_data_query_routes_to_query_service(self) -> None:
        class FakeQueryService:
            def runtime_status(self):
                return {"fake": True}

            def answer(self, text: str) -> DataQueryResult:
                return DataQueryResult(
                    kind="signal_summary",
                    message=f"answered: {text}",
                    evidence={"ok": True},
                )

        with tempfile.TemporaryDirectory() as temp_dir:
            settings = LicenseAgentSettings(
                app_db_path=str(Path(temp_dir) / "app.sqlite3"),
                report_output_root=str(Path(temp_dir) / "reports"),
            )
            service = TeamsChatService(settings, data_query_service=FakeQueryService(), run_async=False)
            response = service.handle_message("What are the strongest violation signals?", "analyst@example.com")
            self.assertEqual(response["type"], "data_query")
            self.assertEqual(response["query_kind"], "signal_summary")
            self.assertIn("answered", response["message"])

    def test_report_status_clarifies_when_live_data_is_not_connected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = LicenseAgentSettings(
                app_db_path=str(Path(temp_dir) / "app.sqlite3"),
                report_output_root=str(Path(temp_dir) / "reports"),
            )
            service = TeamsChatService(settings, run_async=False)
            request = service.handle_message("company Example Corp", "analyst@example.com")
            response = service.handle_message(f"status {request['job_id']}", "analyst@example.com")
            self.assertIn("not enough connected data", response["message"])

    def test_report_job_uses_connected_usage_summary(self) -> None:
        usage_summary = {
            "company_name": "Mediterranean Shipping Company Pty Ltd",
            "license_ids": ["66304944"],
            "files_processed": 66827,
            "links_processed": 286515,
            "file_size_gib": 17.89,
            "file_size_in_bytes": 19214129332,
            "run_count": 59,
            "machine_count": 2,
            "machine_names": ["ZA031DURL1010", "ZA031EW1DAPP010"],
            "mac_count": 3,
            "mac_addresses": ["00090FAA0001", "0022488921F8", "088E90D80D5B"],
            "public_ip_count": 6,
            "public_ips": ["102.182.5.223", "169.1.52.132"],
            "first_start_time": "2025-07-09 10:27:51",
            "last_end_time": "2026-01-17 03:28:12",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = LicenseAgentSettings(
                app_db_path=str(Path(temp_dir) / "app.sqlite3"),
                report_output_root=str(Path(temp_dir) / "reports"),
            )
            service = TeamsChatService(
                settings,
                data_query_service=FakeReportQueryService(usage_summary),
                run_async=False,
            )
            request = service.handle_message(
                "Can you give me a full report on Mediterranean Shipping Company Pty Ltd. "
                "I am looking for possible evidence that they might be violation the LinkTek EULA.",
                "analyst@example.com",
            )
            self.assertIn("**License Violation Review: Mediterranean Shipping Company Pty Ltd**", request["message"])
            self.assertIn("66,827", request["message"])
            self.assertNotIn("Ask for `status", request["message"])
            self.assertNotIn("job-", request["message"])
            response = service.handle_message(f"status {request['job_id']}", "analyst@example.com")

        self.assertIn("**License Violation Review: Mediterranean Shipping Company Pty Ltd**", response["message"])
        self.assertIn("Files processed: 66,827", response["message"])
        self.assertNotIn("job-", response["message"])
        self.assertIn("License usage appears on multiple MAC addresses", response["message"])
        self.assertTrue(response["job"]["result"]["data_connected"])

    def test_report_includes_ip_geolocation_cache_when_connected(self) -> None:
        usage_summary = {
            "company_name": "Example Corp",
            "license_ids": ["66000000"],
            "files_processed": 1,
            "links_processed": 2,
            "file_size_gib": 0.1,
            "file_size_in_bytes": 100,
            "run_count": 1,
            "machine_count": 1,
            "mac_count": 1,
            "public_ip_count": 1,
            "public_ips": ["1.1.1.1"],
        }
        ip_records = {
            "1.1.1.1": {
                "city": "Sydney",
                "region": "New South Wales",
                "country": "Australia",
                "accuracy_radius_km": 20,
            }
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = LicenseAgentSettings(
                app_db_path=str(Path(temp_dir) / "app.sqlite3"),
                report_output_root=str(Path(temp_dir) / "reports"),
            )
            service = TeamsChatService(
                settings,
                data_query_service=FakeReportQueryService(usage_summary, ip_records=ip_records),
                run_async=False,
            )
            response = service.handle_message("company Example Corp", "analyst@example.com")

        self.assertIn("**IP Geolocation Evidence**", response["message"])
        self.assertIn("1.1.1.1 (AWS usage): Sydney, New South Wales, Australia, radius 20 km", response["message"])
        self.assertNotIn("IP geolocation is missing", response["message"])

    def test_ambiguous_company_search_asks_for_clarification_then_uses_selection(self) -> None:
        first = {
            "company_name": "Mediterranean Shipping Company Pty Ltd",
            "company_key": "mediterranean shipping company pty ltd",
            "license_ids": ["66304944"],
            "score": 0.79,
            "files_processed": 66827,
            "links_processed": 286515,
            "file_size_gib": 17.89,
            "file_size_in_bytes": 19214129332,
            "run_count": 59,
            "machine_count": 2,
            "mac_count": 3,
            "public_ip_count": 6,
        }
        second = {
            "company_name": "MSC Mediterranean Shipping Company SA",
            "company_key": "msc mediterranean shipping company sa",
            "license_ids": ["66000000"],
            "score": 0.76,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = LicenseAgentSettings(
                app_db_path=str(Path(temp_dir) / "app.sqlite3"),
                report_output_root=str(Path(temp_dir) / "reports"),
            )
            service = TeamsChatService(
                settings,
                data_query_service=FakeReportQueryService(match=None),
                run_async=False,
            )
            service.data_query_service.usage_client = FakeUsageClient(match=None, candidates=[first, second])
            response = service.handle_message("Report on Mediterranean Shipping", "analyst@example.com")

            self.assertEqual(response["type"], "company_clarification")
            self.assertIn("Which one should I use", response["message"])
            self.assertIn("1. Mediterranean Shipping Company Pty Ltd", response["message"])

            service.data_query_service.usage_client = FakeUsageClient(match=first)
            selected = service.handle_message("use 1", "analyst@example.com")

        self.assertEqual(selected["type"], "report_requested")
        self.assertIn("**License Violation Review: Mediterranean Shipping Company Pty Ltd**", selected["message"])

    def test_report_uses_crm_licensed_personnel_for_eula_threshold(self) -> None:
        summary = {
            "company_name": "Mediterranean Shipping Company Pty Ltd",
            "license_ids": ["66304944"],
            "files_processed": 66827,
            "links_processed": 286515,
            "file_size_gib": 17.89,
            "file_size_in_bytes": 19214129332,
            "run_count": 59,
            "machine_count": 1,
            "mac_count": 1,
            "public_ip_count": 0,
        }
        crm_context = {
            "configured": True,
            "error": "",
            "license_lookup": {
                "rows": [
                    {
                        "name": "Gold -- LFA",
                        "company_name": "Mediterranean Shipping Company Pty Ltd",
                        "product": "LinkTek",
                        "maintenance_expiry_date": "2026-08-28",
                        "employee_or_computer_count": 10,
                        "estimated_personnel_count": 200000,
                        "which_count_to_use": "Employee",
                        "organization_description": "South Africa Finance Division Only",
                    }
                ]
            },
            "linked_records": {
                "licenses": [
                    {
                        "linked_records": {
                            "license_verifications": [
                                {
                                    "name": "LV-1",
                                    "stage": "Data Gathering",
                                    "personnel_count": 10,
                                    "estimated_personnel_count": 200000,
                                    "organization_definition": "South Africa Finance Division Only",
                                }
                            ]
                        }
                    }
                ]
            },
        }

        result = build_usage_report_result(
            "company",
            "Mediterranean Shipping Company Pty Ltd",
            summary,
            {"source_rows": 59, "company_count": 1},
            crm_context=crm_context,
            solo_context={"configured": True, "metrics": {"activations": 1}},
            ip_geolocation_context={"records": {}},
        )

        report_text = result["report_text"]
        self.assertIn("Entitlement denominator: 10 from CRM Customer License employee_or_computer_count (Employee)", report_text)
        self.assertIn("about 1.79 GiB per entitlement unit", report_text)
        self.assertIn("entitlement denominator 10 (Employee)", report_text)
        self.assertIn("estimated personnel 200000", report_text)
        self.assertNotIn("Licensed personnel count is missing", report_text)

    def test_report_performs_automated_scope_and_prior_review_checks(self) -> None:
        summary = {
            "company_name": "Rockwool Bv",
            "license_ids": ["63818402"],
            "files_processed": 100,
            "links_processed": 200,
            "file_size_gib": 500,
            "file_size_in_bytes": 536870912000,
            "run_count": 5,
            "machine_count": 2,
            "mac_count": 2,
            "public_ip_count": 0,
        }
        crm_context = {
            "configured": True,
            "error": "",
            "license_lookup": {
                "rows": [
                    {
                        "name": "Gold -- LFA",
                        "license_code": "63818402",
                        "company_name": "Rockwool A/S",
                        "active_license": False,
                        "maintenance_expiry_date": "2025-05-20",
                        "which_count_to_use": "Employee",
                        "employee_or_computer_count": 125,
                        "organization_description": "Rockwool BV, located at Industrieweg 15, JG Roermond, Netherlands",
                    }
                ]
            },
            "linked_records": {
                "licenses": [
                    {
                        "linked_records": {
                            "sales_routing_forms": [
                                {
                                    "name": "SRF-1",
                                    "license_violation": True,
                                    "unresolved_license_violation": True,
                                }
                            ],
                            "license_verifications": [],
                            "quote_line_item_sets": [{"quantity": 125}],
                        }
                    }
                ]
            },
        }
        ip_context = {
            "records": {
                "154.14.23.182": {
                    "city": "Wijnegem",
                    "region": "Flanders",
                    "country": "Belgium",
                    "accuracy_radius_km": 20,
                }
            },
            "activation_ips": ["154.14.23.182"],
        }

        result = build_usage_report_result(
            "company",
            "Rockwool",
            summary,
            {"source_rows": 5, "company_count": 1},
            crm_context=crm_context,
            solo_context={"configured": True, "metrics": {"activations": 1, "license_ids": ["63818402"]}},
            ip_geolocation_context=ip_context,
        )

        report_text = result["report_text"]
        finding_codes = {item["code"] for item in result["findings"]}
        self.assertIn("**Automated Consistency Checks**", report_text)
        self.assertIn("parsed allowed countries netherlands; regions/states limburg; cities roermond", report_text.lower())
        self.assertIn("Geography scope check: 1 geolocated IP location(s) appear outside", report_text)
        self.assertIn("Prior CRM review check: found 1 prior license-violation signal", report_text)
        self.assertIn("Count consistency check: comparable CRM entitlement/count signals are consistent", report_text)
        self.assertIn("geography_outside_crm_scope", finding_codes)
        self.assertIn("prior_crm_violation_signal", finding_codes)
        self.assertNotIn("Recommended Next Review Steps", report_text)
        self.assertIn("**Human Review Still Needed**", report_text)

    def test_report_flags_usage_over_threshold_when_crm_personnel_is_available(self) -> None:
        summary = {
            "company_name": "Example Corp",
            "license_ids": ["66000000"],
            "files_processed": 10,
            "links_processed": 20,
            "file_size_gib": 600,
            "file_size_in_bytes": 644245094400,
            "run_count": 2,
            "machine_count": 1,
            "mac_count": 1,
            "public_ip_count": 0,
        }
        crm_context = {
            "configured": True,
            "error": "",
            "license_lookup": {"rows": [{"employee_or_computer_count": 5, "which_count_to_use": "Employee"}]},
            "linked_records": {"licenses": []},
        }

        result = build_usage_report_result(
            "company",
            "Example Corp",
            summary,
            {"source_rows": 2, "company_count": 1},
            crm_context=crm_context,
            solo_context={"configured": True, "metrics": {"activations": 1}},
            ip_geolocation_context={"records": {}},
        )

        finding_codes = {item["code"] for item in result["findings"]}
        self.assertIn("usage_over_eula_review_threshold", finding_codes)
        self.assertEqual(result["evaluation"], "review recommended")

    def test_report_can_use_quote_line_item_quantity_when_license_verification_count_is_missing(self) -> None:
        summary = {
            "company_name": "Example Corp",
            "license_ids": ["66000000"],
            "files_processed": 10,
            "links_processed": 20,
            "file_size_gib": 300,
            "file_size_in_bytes": 322122547200,
            "run_count": 2,
            "machine_count": 1,
            "mac_count": 1,
            "public_ip_count": 0,
        }
        crm_context = {
            "configured": True,
            "error": "",
            "license_lookup": {"rows": [{"which_count_to_use": "Employee"}]},
            "linked_records": {
                "licenses": [
                    {
                        "linked_records": {
                            "license_verifications": [],
                            "quote_line_item_sets": [{"quantity": 3}],
                        }
                    }
                ]
            },
        }

        result = build_usage_report_result(
            "company",
            "Example Corp",
            summary,
            {"source_rows": 2, "company_count": 1},
            crm_context=crm_context,
            solo_context={"configured": True, "metrics": {"activations": 1}},
            ip_geolocation_context={"records": {}},
        )

        self.assertIn("Entitlement denominator: 3 from CRM quote line item quantity", result["report_text"])
        self.assertIn("Quote line items: entitlement quantity signal 3", result["report_text"])

    def test_report_uses_customer_license_subset_and_entity_fields_from_which_count_to_use(self) -> None:
        summary = {
            "company_name": "LinkFixer Example",
            "license_ids": ["70006914"],
            "files_processed": 10,
            "links_processed": 20,
            "file_size_gib": 170,
            "file_size_in_bytes": 182536110080,
            "run_count": 2,
            "machine_count": 1,
            "mac_count": 1,
            "public_ip_count": 0,
        }
        crm_context = {
            "configured": True,
            "error": "",
            "license_lookup": {
                "rows": [
                    {
                        "name": "LinkFixer",
                        "license_code": "70006914",
                        "solo_password_present": True,
                        "serial_number": "SER-70006914",
                        "product": "LinkFixer",
                        "maintenance_expiry_date": "2027-01-01",
                        "link_limit": 0,
                        "employee_or_computer_count": 1700,
                        "which_count_to_use": "Employee",
                        "site_count": None,
                        "subset_license_count": 1700,
                        "subset_price_multiplier_3": 70,
                        "entire_legal_entity_personnel_count_3": 1700,
                        "entire_legal_entity_price_multiplier_3": 30,
                    }
                ]
            },
            "linked_records": {"licenses": []},
        }

        result = build_usage_report_result(
            "company",
            "LinkFixer Example",
            summary,
            {"source_rows": 2, "company_count": 1},
            crm_context=crm_context,
            solo_context={"configured": True, "metrics": {"activations": 1}},
            ip_geolocation_context={"records": {}},
        )

        report_text = result["report_text"]
        self.assertIn("Entitlement denominator: 1,700 from CRM Customer License employee_or_computer_count (Employee)", report_text)
        self.assertIn("license code 70006914", report_text)
        self.assertIn("SOLO password present", report_text)
        self.assertNotIn("MER4457", report_text)
        self.assertIn("employee/computer count 1700", report_text)
        self.assertIn("subset license count 1700", report_text)
        self.assertIn("entire legal entity personnel 1700", report_text)
