import json
import tempfile
from pathlib import Path
from unittest import TestCase

from license_agent.settings import LicenseAgentSettings
from license_agent.usage_summary import UsageSummaryClient


class UsageSummaryClientTests(TestCase):
    def test_ambiguous_fuzzy_company_search_returns_candidates_without_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary_path = Path(temp_dir) / "usage.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "meta": {"company_count": 2},
                        "companies": {
                            "mediterranean shipping company": {
                                "company_key": "mediterranean shipping company",
                                "company_name": "MEDITERRANEAN SHIPPING COMPANY PTY LTD",
                                "company_names": ["MEDITERRANEAN SHIPPING COMPANY PTY LTD"],
                                "license_ids": ["66304944"],
                            },
                            "msc mediterranean shipping company": {
                                "company_key": "msc mediterranean shipping company",
                                "company_name": "MSC MEDITERRANEAN SHIPPING COMPANY SA",
                                "company_names": ["MSC MEDITERRANEAN SHIPPING COMPANY SA"],
                                "license_ids": ["66000000"],
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            client = UsageSummaryClient(LicenseAgentSettings(usage_summary_local_path=str(summary_path)))
            result = client.find_company("Mediterranean Shipping")

        self.assertIsNone(result["match"])
        self.assertGreaterEqual(len(result["candidates"]), 2)
        self.assertEqual(result["candidates"][0]["company_name"], "MEDITERRANEAN SHIPPING COMPANY PTY LTD")

    def test_confident_fuzzy_company_search_returns_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            summary_path = Path(temp_dir) / "usage.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "companies": {
                            "hudson housing capital": {
                                "company_key": "hudson housing capital",
                                "company_name": "Hudson Housing Capital LLC",
                                "company_names": ["Hudson Housing Capital LLC"],
                                "license_ids": ["66275132"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            client = UsageSummaryClient(LicenseAgentSettings(usage_summary_local_path=str(summary_path)))
            result = client.find_company("Hudson Housing")

        self.assertEqual(result["match"]["company_name"], "Hudson Housing Capital LLC")
