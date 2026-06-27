from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
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


class GeoLite2GeoLocator:
    """Offline MaxMind GeoLite2 City `.mmdb` reader."""

    def __init__(self, database_path: str | Path, *, provider_version: str | None = None) -> None:
        self.database_path = str(database_path)
        self.provider_version = provider_version or Path(database_path).name

    def lookup(self, ip_address: str) -> GeoLocation | None:
        import maxminddb

        with maxminddb.open_database(self.database_path) as reader:
            payload = reader.get(ip_address)
        if not payload:
            return None

        city = _localized_name(payload.get("city"))
        subdivisions = payload.get("subdivisions") or []
        state = _localized_name(subdivisions[0]) if subdivisions else None
        country = _localized_name(payload.get("country")) or _localized_name(payload.get("registered_country"))
        location = payload.get("location") or {}
        return GeoLocation(
            ip_address=ip_address,
            city=city,
            state=state,
            country=country,
            latitude=_float_or_none(location.get("latitude")),
            longitude=_float_or_none(location.get("longitude")),
            accuracy_radius_km=_float_or_none(location.get("accuracy_radius")),
            provider="maxmind_geolite2_city",
            looked_up_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )


def geolocation_cache_record(
    ip_address: str,
    location: GeoLocation | None,
    *,
    source: str,
    provider_version: str,
) -> dict[str, object]:
    looked_up_at = datetime.now(timezone.utc).isoformat()
    if location is None:
        return {
            "ip_address": ip_address,
            "source": source,
            "provider": "maxmind_geolite2_city",
            "provider_version": provider_version,
            "lookup_status": "not_found",
            "lookup_date": looked_up_at,
            "city": "",
            "region": "",
            "country": "",
            "latitude": "",
            "longitude": "",
            "accuracy_radius_km": "",
            "confidence_notes": "GeoLite2 did not return a city record for this IP.",
        }

    payload = asdict(location)
    return {
        "ip_address": ip_address,
        "source": source,
        "provider": payload.get("provider") or "maxmind_geolite2_city",
        "provider_version": provider_version,
        "lookup_status": "found",
        "lookup_date": looked_up_at,
        "city": payload.get("city") or "",
        "region": payload.get("state") or "",
        "country": payload.get("country") or "",
        "latitude": payload.get("latitude") if payload.get("latitude") is not None else "",
        "longitude": payload.get("longitude") if payload.get("longitude") is not None else "",
        "accuracy_radius_km": (
            payload.get("accuracy_radius_km") if payload.get("accuracy_radius_km") is not None else ""
        ),
        "confidence_notes": (
            "GeoLite2 city-level location is approximate. Do not treat it as a street address or exact office location."
        ),
    }


def _float_or_none(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _localized_name(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    names = payload.get("names")
    if isinstance(names, dict):
        value = names.get("en")
        if isinstance(value, str) and value:
            return value
    value = payload.get("name")
    return value if isinstance(value, str) and value else None
