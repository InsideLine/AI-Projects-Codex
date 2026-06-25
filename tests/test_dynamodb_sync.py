import json
import threading
import tempfile
from pathlib import Path
from unittest import TestCase

from license_agent.dynamodb_sync import (
    load_checkpoints,
    save_checkpoints,
    sync_dynamodb_table,
    sync_dynamodb_table_parallel,
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


class ParallelFakeDynamoDbClient:
    def __init__(self, responses_by_segment):
        self.responses_by_segment = {segment: list(responses) for segment, responses in responses_by_segment.items()}
        self.calls = []
        self._lock = threading.Lock()

    def scan(self, **kwargs):
        segment = kwargs["Segment"]
        with self._lock:
            self.calls.append(kwargs)
            return self.responses_by_segment[segment].pop(0)


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

    def test_parallel_sync_tracks_segment_checkpoints(self) -> None:
        responses_by_segment = {
            0: [
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
            ],
            1: [
                {
                    "Items": [{"LicenseId": {"N": "3"}}],
                    "Count": 1,
                    "ScannedCount": 1,
                }
            ],
        }
        client = ParallelFakeDynamoDbClient(responses_by_segment)

        with tempfile.TemporaryDirectory() as temp_dir:
            landing_zone = FilesystemLandingZone(temp_dir)
            result = sync_dynamodb_table_parallel(
                client,
                landing_zone,
                table_name="ProcessInfo",
                source_account="104059960856",
                total_segments=2,
                page_limit=100,
            )

            self.assertTrue(result.complete)
            self.assertEqual(result.records_persisted, 3)
            self.assertEqual(result.batches_persisted, 3)
            self.assertIsNotNone(result.segment_states)
            self.assertEqual(set(result.segment_states.keys()), {"0", "1"})
            self.assertTrue(all(call["TotalSegments"] == 2 for call in client.calls))

            checkpoints = update_checkpoint_for_result({}, result)
            self.assertEqual(checkpoints["ProcessInfo"]["mode"], "parallel_scan")
            self.assertEqual(set(checkpoints["ProcessInfo"]["segments"].keys()), {"0", "1"})
