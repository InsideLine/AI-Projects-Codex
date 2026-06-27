import json
import tempfile
from pathlib import Path
from unittest import TestCase

from license_agent.geolocation import geolocation_cache_record
from license_agent.ip_geolocation_cache import IpGeolocationCacheClient
from license_agent.models import GeoLocation
from license_agent.settings import LicenseAgentSettings


class IpGeolocationCacheTests(TestCase):
    def test_lookup_many_reads_local_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "ip_cache.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "meta": {"provider": "maxmind_geolite2_city"},
                        "ips": {
                            "1.1.1.1": {
                                "ip_address": "1.1.1.1",
                                "lookup_status": "found",
                                "city": "Sydney",
                                "region": "New South Wales",
                                "country": "Australia",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            client = IpGeolocationCacheClient(
                LicenseAgentSettings(ip_geolocation_cache_local_path=str(cache_path))
            )
            result = client.lookup_many(["1.1.1.1", "8.8.8.8"])

        self.assertTrue(result["configured"])
        self.assertEqual(list(result["records"]), ["1.1.1.1"])
        self.assertEqual(result["records"]["1.1.1.1"]["city"], "Sydney")

    def test_geolocation_cache_record_preserves_approximation_warning(self) -> None:
        record = geolocation_cache_record(
            "1.1.1.1",
            GeoLocation(
                ip_address="1.1.1.1",
                city="Sydney",
                state="New South Wales",
                country="Australia",
                latitude=-33.8688,
                longitude=151.2093,
                accuracy_radius_km=20,
                provider="maxmind_geolite2_city",
            ),
            source="test",
            provider_version="GeoLite2-City.mmdb",
        )

        self.assertEqual(record["lookup_status"], "found")
        self.assertEqual(record["city"], "Sydney")
        self.assertIn("approximate", record["confidence_notes"])
