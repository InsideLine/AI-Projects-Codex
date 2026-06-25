import tempfile
from pathlib import Path
from unittest import TestCase

from license_agent.data_query import DataQueryResult
from license_agent.settings import LicenseAgentSettings
from license_agent.teams_service import TeamsChatService, parse_intent


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
            self.assertIn(job_id, history["message"])

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
