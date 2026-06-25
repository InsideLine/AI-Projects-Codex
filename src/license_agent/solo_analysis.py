from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Iterable

from .correlation_analysis import classify_verification, normalize_company_name


CSV_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


@dataclass(frozen=True)
class SoloCompanyMetric:
    company_key: str
    company_name: str
    activations: int
    successful_activations: int
    rejected_activations: int
    rejection_rate: float
    unique_ips: int
    unique_installations: int
    unique_computers: int
    deactivations: int
    unique_licenses: int


def read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    csv_path = Path(path)
    last_error: Exception | None = None
    for encoding in CSV_ENCODINGS:
        try:
            with csv_path.open(encoding=encoding, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    return []


def read_csv_rows_many(paths: Iterable[str | Path], *, dedupe: bool = False) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for path in paths:
        for row in read_csv_rows(path):
            if not dedupe:
                rows.append(row)
                continue
            fingerprint = tuple(sorted((key, value or "") for key, value in row.items()))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            rows.append(row)
    return rows


def find_latest_csv(root: str | Path) -> Path | None:
    candidates = find_all_csvs(root)
    return candidates[-1] if candidates else None


def find_all_csvs(root: str | Path) -> list[Path]:
    candidates: list[Path] = []
    for path in Path(root).rglob("*.csv"):
        if "__MACOSX" in path.parts:
            continue
        if path.name.startswith("._"):
            continue
        candidates.append(path)
    return sorted(candidates)


def build_company_metrics(
    activation_rows: Iterable[dict[str, str]],
    *,
    cutoff_by_company: dict[str, datetime] | None = None,
) -> dict[str, SoloCompanyMetric]:
    cutoff_by_company = cutoff_by_company or {}
    grouped: dict[str, list[dict[str, str]]] = {}

    for row in activation_rows:
        company_name = (row.get("CompanyName") or "").strip()
        if not company_name:
            continue
        company_key = normalize_company_name(company_name)
        if not company_key:
            continue

        activation_date = _parse_iso_datetime(row.get("ActivationDate"))
        cutoff = cutoff_by_company.get(company_key)
        if cutoff is not None and (activation_date is None or activation_date >= cutoff):
            continue

        grouped.setdefault(company_key, []).append(row)

    metrics: dict[str, SoloCompanyMetric] = {}
    for company_key, rows in grouped.items():
        company_names = Counter((row.get("CompanyName") or "").strip() for row in rows if row.get("CompanyName"))
        successful = sum(1 for row in rows if (row.get("Status") or "").strip() == "Successful")
        rejected = sum(1 for row in rows if (row.get("Status") or "").strip() == "Rejected")
        unique_ips = {row.get("IPAddress", "").strip() for row in rows if row.get("IPAddress")}
        unique_installations = {row.get("InstallationID", "").strip() for row in rows if row.get("InstallationID")}
        unique_computers = {row.get("ComputerID", "").strip() for row in rows if row.get("ComputerID")}
        deactivations = sum(1 for row in rows if (row.get("DeactivatedDate") or "").strip())
        unique_licenses = {row.get("LicenseID", "").strip() for row in rows if row.get("LicenseID")}

        metrics[company_key] = SoloCompanyMetric(
            company_key=company_key,
            company_name=company_names.most_common(1)[0][0],
            activations=len(rows),
            successful_activations=successful,
            rejected_activations=rejected,
            rejection_rate=(rejected / len(rows)) if rows else 0.0,
            unique_ips=len(unique_ips),
            unique_installations=len(unique_installations),
            unique_computers=len(unique_computers),
            deactivations=deactivations,
            unique_licenses=len(unique_licenses),
        )
    return metrics


def summarize_metrics(
    metrics: dict[str, SoloCompanyMetric],
    company_keys: Iterable[str],
) -> dict[str, float | int] | None:
    selected = [metrics[key] for key in company_keys if key in metrics]
    if not selected:
        return None

    return {
        "company_count": len(selected),
        "median_activations": median(item.activations for item in selected),
        "median_rejection_rate": median(item.rejection_rate for item in selected),
        "median_unique_ips": median(item.unique_ips for item in selected),
        "median_unique_installations": median(item.unique_installations for item in selected),
        "median_unique_computers": median(item.unique_computers for item in selected),
        "median_deactivations": median(item.deactivations for item in selected),
        "median_unique_licenses": median(item.unique_licenses for item in selected),
        "share_with_rejections": _share(selected, lambda item: item.rejected_activations > 0),
        "share_with_multi_ip": _share(selected, lambda item: item.unique_ips > 1),
        "share_with_multi_installation": _share(selected, lambda item: item.unique_installations > 1),
        "share_with_deactivation": _share(selected, lambda item: item.deactivations > 0),
    }


def load_license_verification_cutoffs(path: str | Path) -> tuple[dict[str, datetime], dict[str, str]]:
    records = read_csv_rows(path)
    cutoff_by_company: dict[str, datetime] = {}
    name_by_company: dict[str, str] = {}
    for record in records:
        company_name = (record.get("Company Name") or "").strip()
        if not company_name:
            continue
        if classify_verification(
            stage=(record.get("Stage") or "").strip(),
            current_status=(record.get("Current Status") or "").strip(),
        ) != "violation":
            continue
        created_time = _parse_zoho_datetime(record.get("Created Time"))
        if created_time is None:
            continue
        company_key = normalize_company_name(company_name)
        previous = cutoff_by_company.get(company_key)
        if previous is None or created_time < previous:
            cutoff_by_company[company_key] = created_time
            name_by_company[company_key] = company_name
    return cutoff_by_company, name_by_company


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _parse_zoho_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    for fmt in ("%b %d, %Y %I:%M %p", "%B %d, %Y %I:%M %p"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def _share(items: list[SoloCompanyMetric], predicate) -> float:
    if not items:
        return 0.0
    matches = sum(1 for item in items if predicate(item))
    return matches / len(items)
