from __future__ import annotations

import json
from datetime import datetime
from typing import Protocol
from urllib.request import Request, urlopen

from .models import GeoLocation


class GeoLocator(Protocol):
    def lookup(self, ip_address: str) -> GeoLocation | None:
        ...


class ManualGeoLocator:
    """Small offline provider for tests, backfills, and analyst-entered locations."""

    def __init__(self, locations: dict[str, GeoLocation]) -> None:
        self.locations = locations

    def lookup(self, ip_address: str) -> GeoLocation | None:
        return self.locations.get(ip_address)


class IPinfoGeoLocator:
    def __init__(self, token: str) -> None:
        self.token = token

    def lookup(self, ip_address: str) -> GeoLocation | None:
        request = Request(f"https://api.ipinfo.io/lookup/{ip_address}?token={self.token}")
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        geo = payload.get("geo") or payload
        return GeoLocation(
            ip_address=ip_address,
            city=geo.get("city"),
            state=geo.get("region"),
            country=geo.get("country") or geo.get("country_name"),
            latitude=_float_or_none(geo.get("latitude")),
            longitude=_float_or_none(geo.get("longitude")),
            accuracy_radius_km=_float_or_none(geo.get("radius")),
            provider="ipinfo",
            looked_up_at=datetime.utcnow(),
        )


class MaxMindDbGeoLocator:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    def lookup(self, ip_address: str) -> GeoLocation | None:
        import geoip2.database
        import geoip2.errors

        try:
            with geoip2.database.Reader(self.database_path) as reader:
                response = reader.city(ip_address)
        except geoip2.errors.AddressNotFoundError:
            return None

        return GeoLocation(
            ip_address=ip_address,
            city=response.city.name,
            state=(response.subdivisions.most_specific.name if response.subdivisions else None),
            country=response.country.name,
            latitude=response.location.latitude,
            longitude=response.location.longitude,
            accuracy_radius_km=response.location.accuracy_radius,
            provider="maxmind",
            looked_up_at=datetime.utcnow(),
        )


def _float_or_none(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)

