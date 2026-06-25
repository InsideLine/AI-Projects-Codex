import tempfile
from datetime import datetime
from pathlib import Path
from unittest import TestCase

from license_agent.solo_analysis import (
    build_company_metrics,
    find_all_csvs,
    find_latest_csv,
    read_csv_rows,
    read_csv_rows_many,
    summarize_metrics,
)


class SoloAnalysisTests(TestCase):
    def test_read_csv_rows_supports_cp1252(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.csv"
            path.write_bytes("CompanyName,LicenseID\nTchibo GmbH,123\n".encode("cp1252"))
            rows = read_csv_rows(path)
            self.assertEqual(rows[0]["CompanyName"], "Tchibo GmbH")

    def test_build_company_metrics_applies_cutoff(self) -> None:
        rows = [
            {
                "CompanyName": "Example Corp",
                "ActivationDate": "2025-01-01T00:00:00",
                "Status": "Successful",
                "IPAddress": "1.1.1.1",
                "InstallationID": "A",
                "ComputerID": "1000",
                "DeactivatedDate": "",
                "LicenseID": "123",
            },
            {
                "CompanyName": "Example Corp",
                "ActivationDate": "2025-03-01T00:00:00",
                "Status": "Rejected",
                "IPAddress": "2.2.2.2",
                "InstallationID": "B",
                "ComputerID": "1000",
                "DeactivatedDate": "",
                "LicenseID": "123",
            },
        ]
        metrics = build_company_metrics(
            rows,
            cutoff_by_company={"example": datetime(2025, 2, 1)},
        )
        metric = metrics["example"]
        self.assertEqual(metric.activations, 1)
        self.assertEqual(metric.rejected_activations, 0)

    def test_summarize_metrics_reports_shares(self) -> None:
        rows = [
            {
                "CompanyName": "Example Corp",
                "ActivationDate": "2025-01-01T00:00:00",
                "Status": "Rejected",
                "IPAddress": "1.1.1.1",
                "InstallationID": "A",
                "ComputerID": "1000",
                "DeactivatedDate": "2025-01-02T00:00:00",
                "LicenseID": "123",
            },
            {
                "CompanyName": "Other Corp",
                "ActivationDate": "2025-01-01T00:00:00",
                "Status": "Successful",
                "IPAddress": "1.1.1.1",
                "InstallationID": "A",
                "ComputerID": "1000",
                "DeactivatedDate": "",
                "LicenseID": "456",
            },
        ]
        metrics = build_company_metrics(rows)
        summary = summarize_metrics(metrics, metrics.keys())
        assert summary is not None
        self.assertEqual(summary["company_count"], 2)
        self.assertEqual(summary["share_with_rejections"], 0.5)
        self.assertEqual(summary["share_with_deactivation"], 0.5)

    def test_read_csv_rows_many_can_dedupe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.csv"
            second = Path(temp_dir) / "second.csv"
            content = "CompanyName,LicenseID\nExample Corp,123\n"
            first.write_text(content, encoding="utf-8")
            second.write_text(content, encoding="utf-8")
            rows = read_csv_rows_many([first, second], dedupe=True)
            self.assertEqual(len(rows), 1)

    def test_find_latest_csv_and_find_all_csvs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "2026" / "06" / "23" / "manual_drop_001" / "a.csv"
            second = root / "2026" / "06" / "24" / "manual_drop_002" / "b.csv"
            macos = root / "2026" / "06" / "24" / "manual_drop_002" / "__MACOSX" / "._c.csv"
            second.parent.mkdir(parents=True, exist_ok=True)
            first.parent.mkdir(parents=True, exist_ok=True)
            macos.parent.mkdir(parents=True, exist_ok=True)
            first.write_text("x\n1\n", encoding="utf-8")
            second.write_text("x\n2\n", encoding="utf-8")
            macos.write_text("x\n3\n", encoding="utf-8")
            all_paths = find_all_csvs(root)
            latest = find_latest_csv(root)
            self.assertEqual(len(all_paths), 2)
            self.assertEqual(latest, second)
