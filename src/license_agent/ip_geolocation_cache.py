from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .settings import LicenseAgentSettings


class IpGeolocationCacheClient:
    def __init__(self, settings: LicenseAgentSettings) -> None:
        self.settings = settings
        self._cache: dict[str, Any] | None = None

    def status(self) -> dict[str, Any]:
        return {
            "configured": bool(self.settings.ip_geolocation_cache_local_path or self.settings.raw_s3_bucket),
            "local_path": self.settings.ip_geolocation_cache_local_path,
            "s3_bucket": self.settings.raw_s3_bucket,
            "s3_key": self.settings.ip_geolocation_cache_s3_key,
        }

    def lookup_many(self, ip_addresses: list[str]) -> dict[str, Any]:
        status = self.status()
        try:
            cache = self._load_cache()
        except Exception as exc:  # pragma: no cover - live S3 depends on runtime config
            return {**status, "error": f"{type(exc).__name__}: {exc}", "records": {}}
        if not cache:
            return {**status, "configured": False, "error": "", "records": {}, "meta": {}}

        records_by_ip = cache.get("ips") or {}
        records = {
            ip_address: records_by_ip[ip_address]
            for ip_address in ip_addresses
            if ip_address in records_by_ip
        }
        return {
            **status,
            "configured": True,
            "error": "",
            "records": records,
            "meta": cache.get("meta") or {},
        }

    def _load_cache(self) -> dict[str, Any] | None:
        if self._cache is not None:
            return self._cache

        if self.settings.ip_geolocation_cache_local_path:
            path = Path(self.settings.ip_geolocation_cache_local_path)
            if path.exists():
                self._cache = json.loads(path.read_text(encoding="utf-8"))
                return self._cache

        if self.settings.raw_s3_bucket:
            self._cache = self._load_cache_from_s3()
            return self._cache

        return None

    def _load_cache_from_s3(self) -> dict[str, Any]:
        import boto3

        client = boto3.client("s3", region_name=self.settings.aws_region)
        response = client.get_object(
            Bucket=self.settings.raw_s3_bucket,
            Key=self.settings.ip_geolocation_cache_s3_key,
        )
        return json.loads(response["Body"].read().decode("utf-8"))
