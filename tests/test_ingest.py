import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from license_agent.ingest import FilesystemLandingZone, RawBatch


class IngestTests(TestCase):
    def test_persists_batch_to_filesystem_landing_zone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            landing_zone = FilesystemLandingZone(temp_dir)
            persisted = landing_zone.persist(
                RawBatch(
                    source_system="aws_usage",
                    dataset="customer_usage",
                    records=(
                        {"license_id": "LIC-1", "links_processed": 10},
                        {"license_id": "LIC-2", "links_processed": 12},
                    ),
                    extracted_at=datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc),
                    source_account="123456789012",
                    schema_version="2026-06-18",
                )
            )

            records_path = Path(persisted.records_path)
            manifest_path = Path(persisted.manifest_path)

            self.assertTrue(records_path.exists())
            self.assertTrue(manifest_path.exists())
            self.assertEqual(persisted.record_count, 2)

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["source_system"], "aws_usage")
            self.assertEqual(manifest["dataset"], "customer_usage")
            self.assertEqual(manifest["record_count"], 2)

            lines = records_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
