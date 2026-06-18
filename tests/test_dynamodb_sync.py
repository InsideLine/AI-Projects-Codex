import json
import tempfile
from pathlib import Path
from unittest import TestCase

from license_agent.dynamodb_sync import (
    load_checkpoints,
    save_checkpoints,
    sync_dynamodb_table,
    update_checkpoint_for_result,
)
from license_agent.ingest import FilesystemLandingZone


class FakeDynamoDbClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def scan(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


class DynamoDbSyncTests(TestCase):
    def test_sync_persists_pages_and_returns_resume_key(self) -> None:
        responses = [
            {
                "Items": [{"LicenseId": {"N": "1"}}],
                "Count": 1,
                "ScannedCount": 1,
                "LastEvaluatedKey": {"LicenseId": {"N": "1"}, "Guid": {"S": "abc"}},
            },
            {
                "Items": [{"LicenseId": {"N": "2"}}],
                "Count": 1,
                "ScannedCount": 1,
            },
        ]
        client = FakeDynamoDbClient(responses)

        with tempfile.TemporaryDirectory() as temp_dir:
            landing_zone = FilesystemLandingZone(temp_dir)
            result = sync_dynamodb_table(
                client,
                landing_zone,
                table_name="ProcessInfo",
                source_account="104059960856",
                page_limit=100,
            )

            self.assertTrue(result.complete)
            self.assertEqual(result.records_persisted, 2)
            self.assertEqual(result.batches_persisted, 2)
            self.assertEqual(len(client.calls), 2)

    def test_sync_can_stop_early_and_write_checkpoint(self) -> None:
        responses = [
            {
                "Items": [{"Key": {"S": "one"}}],
                "Count": 1,
                "ScannedCount": 1,
                "LastEvaluatedKey": {"Key": {"S": "one"}, "RunId": {"S": "next"}},
            }
        ]
        client = FakeDynamoDbClient(responses)

        with tempfile.TemporaryDirectory() as temp_dir:
            landing_zone = FilesystemLandingZone(temp_dir)
            result = sync_dynamodb_table(
                client,
                landing_zone,
                table_name="SiteInfo",
                source_account="104059960856",
                page_limit=1,
                max_pages=1,
            )
            self.assertFalse(result.complete)
            self.assertIsNotNone(result.last_evaluated_key)

            checkpoint_path = Path(temp_dir) / "checkpoints.json"
            checkpoints = update_checkpoint_for_result({}, result)
            save_checkpoints(checkpoint_path, checkpoints)
            reloaded = load_checkpoints(checkpoint_path)

            self.assertEqual(
                json.dumps(reloaded["SiteInfo"]["last_evaluated_key"], sort_keys=True),
                json.dumps(result.last_evaluated_key, sort_keys=True),
            )

