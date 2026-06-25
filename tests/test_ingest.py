import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from license_agent.ingest import FilesystemLandingZone, RawBatch, S3LandingZone, build_landing_zone
from license_agent.settings import LicenseAgentSettings


class FakeS3Client:
    def __init__(self) -> None:
        self.objects = {}

    def put_object(self, **kwargs):
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs
        return {}


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

    def test_persists_batch_to_s3_landing_zone(self) -> None:
        client = FakeS3Client()
        landing_zone = S3LandingZone("raw-bucket", s3_client=client)
        persisted = landing_zone.persist(
            RawBatch(
                source_system="aws_dynamodb_weekly",
                dataset="ProcessInfo",
                records=({"LicenseId": {"N": "1"}},),
                source_account="104059960856",
            )
        )

        self.assertEqual(persisted.record_count, 1)
        self.assertTrue(persisted.records_path.startswith("s3://raw-bucket/raw/aws_dynamodb_weekly/processinfo/"))
        self.assertEqual(len(client.objects), 2)
        content_types = {item["ContentType"] for item in client.objects.values()}
        self.assertEqual(content_types, {"application/json", "application/x-ndjson"})

    def test_build_landing_zone_prefers_s3_when_bucket_is_configured(self) -> None:
        settings = LicenseAgentSettings(raw_s3_bucket="raw-bucket")
        with patch("license_agent.ingest.S3LandingZone") as landing_zone_class:
            build_landing_zone(settings)

        landing_zone_class.assert_called_once_with("raw-bucket")
