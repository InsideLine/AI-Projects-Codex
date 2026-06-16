from datetime import date
from unittest import TestCase

from license_agent.settings import LicenseAgentSettings
from license_agent.solo import SoloClient, SoloReportRequest


class SoloClientTests(TestCase):
    def test_builds_license_custom_data_xml(self) -> None:
        settings = LicenseAgentSettings(
            solo_author_id="2512207",
            solo_api_user_id="integration-user",
            solo_api_user_password="integration-password",
        )
        client = SoloClient(settings)

        xml_payload = client.build_get_license_custom_data_xml(66038308)

        self.assertIn("<GetLicenseCustomData>", xml_payload)
        self.assertIn("<AuthorID>2512207</AuthorID>", xml_payload)
        self.assertIn("<LicenseID>66038308</LicenseID>", xml_payload)

    def test_builds_programmatic_report_params(self) -> None:
        settings = LicenseAgentSettings(
            solo_author_id="2512207",
            solo_api_user_id="integration-user",
            solo_api_user_password="integration-password",
        )
        client = SoloClient(settings)

        request = SoloReportRequest(
            report_path="/authors/RptLicensesByProduct.aspx",
            report_type="Csv",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            extra_params={"FilterOKStatus": "True", "SortBy": "1"},
        )
        params = client.build_programmatic_report_params(request)

        self.assertEqual(params["WebServiceLogin"], "True")
        self.assertEqual(params["AuthorID"], "2512207")
        self.assertEqual(params["ReportType"], "Csv")
        self.assertEqual(params["StartDate"], "1/1/2026")
        self.assertEqual(params["EndDate"], "1/31/2026")
        self.assertEqual(params["FilterOKStatus"], "True")
