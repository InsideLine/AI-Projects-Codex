from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from .correlation_analysis import normalize_company_name
from .settings import LicenseAgentSettings


SUMMARY_SCHEMA_VERSION = 1
DEFAULT_USAGE_SUMMARY_KEY = "curated/aws_usage/company_usage_summary.json"


@dataclass
class CompanyUsageAccumulator:
    company_names: Counter[str] = field(default_factory=Counter)
    license_ids: set[str] = field(default_factory=set)
    machine_names: set[str] = field(default_factory=set)
    mac_addresses: set[str] = field(default_factory=set)
    user_names: set[str] = field(default_factory=set)
    public_ips: set[str] = field(default_factory=set)
    tasks: set[str] = field(default_factory=set)
    statuses: set[str] = field(default_factory=set)
    run_count: int = 0
    rows_with_company_name: int = 0
    files_processed: int = 0
    links_processed: int = 0
    file_size_in_bytes: int = 0
    first_start_time: str | None = None
    last_end_time: str | None = None

    def add(self, item: dict[str, Any]) -> None:
        company_name = _string(item, "CompanyName")
        if company_name:
            self.company_names[company_name] += 1
            self.rows_with_company_name += 1
        license_id = _number_or_string(item, "LicenseId")
        if license_id:
            self.license_ids.add(license_id)
        machine_name = _string(item, "MachineName")
        if machine_name:
            self.machine_names.add(machine_name)
        for mac_address in _split_multi_value(_string(item, "MacAddress")):
            self.mac_addresses.add(mac_address.upper())
        user_name = _string(item, "UserName")
        if user_name:
            self.user_names.add(user_name)
        public_ip = _string(item, "PublicIPAddress")
        if public_ip:
            self.public_ips.add(public_ip)
        task = _string(item, "Task")
        if task:
            self.tasks.add(task)
        status = _string(item, "Status")
        if status:
            self.statuses.add(status)

        self.run_count += 1
        self.files_processed += _number(item, "NumFilesProcessed")
        self.links_processed += _number(item, "NumLinksProcessed")
        self.file_size_in_bytes += _number(item, "FileSizeInBytes")

        start_time = _string(item, "StartTime")
        if start_time and (self.first_start_time is None or start_time < self.first_start_time):
            self.first_start_time = start_time
        end_time = _string(item, "EndTime")
        if end_time and (self.last_end_time is None or end_time > self.last_end_time):
            self.last_end_time = end_time

    def to_summary(self, company_key: str) -> dict[str, Any]:
        primary_name = self.company_names.most_common(1)[0][0] if self.company_names else company_key
        return {
            "company_key": company_key,
            "company_name": primary_name,
            "company_names": [name for name, _ in self.company_names.most_common()],
            "license_ids": sorted(self.license_ids),
            "run_count": self.run_count,
            "rows_with_company_name": self.rows_with_company_name,
            "files_processed": self.files_processed,
            "links_processed": self.links_processed,
            "file_size_in_bytes": self.file_size_in_bytes,
            "file_size_gib": round(self.file_size_in_bytes / (1024**3), 2),
            "machine_names": sorted(self.machine_names),
            "machine_count": len(self.machine_names),
            "mac_addresses": sorted(self.mac_addresses),
            "mac_count": len(self.mac_addresses),
            "user_names": sorted(self.user_names),
            "user_count": len(self.user_names),
            "public_ips": sorted(self.public_ips),
            "public_ip_count": len(self.public_ips),
            "tasks": sorted(self.tasks),
            "statuses": sorted(self.statuses),
            "first_start_time": self.first_start_time,
            "last_end_time": self.last_end_time,
        }


class UsageSummaryClient:
    def __init__(self, settings: LicenseAgentSettings) -> None:
        self.settings = settings
        self._summary: dict[str, Any] | None = None

    def status(self) -> dict[str, Any]:
        return {
            "configured": bool(self.settings.usage_summary_local_path or self.settings.raw_s3_bucket),
            "local_path": self.settings.usage_summary_local_path,
            "s3_bucket": self.settings.raw_s3_bucket,
            "s3_key": self.settings.usage_summary_s3_key,
        }

    def find_company(self, company_name: str) -> dict[str, Any]:
        status = self.status()
        try:
            summary = self._load_summary()
        except Exception as exc:  # pragma: no cover - live S3 depends on runtime config
            return {**status, "configured": status["configured"], "error": f"{type(exc).__name__}: {exc}"}
        if not summary:
            return {**status, "configured": False, "error": "", "match": None}

        companies = summary.get("companies") or {}
        query_key = normalize_company_name(company_name)
        match = companies.get(query_key)
        candidates: list[dict[str, Any]] = []
        if match is None:
            candidates = _find_fuzzy_company_candidates(query_key, companies.values())
            if _single_confident_candidate(candidates):
                match = candidates[0]["summary"]
        return {
            **status,
            "configured": True,
            "error": "",
            "match": match,
            "candidates": candidates,
            "summary_meta": summary.get("meta", {}),
        }

    def find_company_candidates(self, company_name: str, *, limit: int = 5) -> dict[str, Any]:
        status = self.status()
        try:
            summary = self._load_summary()
        except Exception as exc:  # pragma: no cover - live S3 depends on runtime config
            return {**status, "configured": status["configured"], "error": f"{type(exc).__name__}: {exc}"}
        if not summary:
            return {**status, "configured": False, "error": "", "candidates": []}

        query_key = normalize_company_name(company_name)
        candidates = _find_fuzzy_company_candidates(query_key, (summary.get("companies") or {}).values(), limit=limit)
        return {**status, "configured": True, "error": "", "candidates": candidates, "summary_meta": summary.get("meta", {})}

    def find_license(self, license_id: str) -> dict[str, Any]:
        status = self.status()
        try:
            summary = self._load_summary()
        except Exception as exc:  # pragma: no cover - live S3 depends on runtime config
            return {**status, "configured": status["configured"], "error": f"{type(exc).__name__}: {exc}"}
        if not summary:
            return {**status, "configured": False, "error": "", "match": None}

        wanted = str(license_id).strip()
        for company_summary in (summary.get("companies") or {}).values():
            if wanted in {str(value).strip() for value in company_summary.get("license_ids") or []}:
                return {
                    **status,
                    "configured": True,
                    "error": "",
                    "match": company_summary,
                    "summary_meta": summary.get("meta", {}),
                }
        return {**status, "configured": True, "error": "", "match": None, "summary_meta": summary.get("meta", {})}

    def _load_summary(self) -> dict[str, Any] | None:
        if self._summary is not None:
            return self._summary

        if self.settings.usage_summary_local_path:
            path = Path(self.settings.usage_summary_local_path)
            if path.exists():
                self._summary = json.loads(path.read_text(encoding="utf-8"))
                return self._summary

        if self.settings.raw_s3_bucket:
            self._summary = self._load_summary_from_s3()
            return self._summary

        return None

    def _load_summary_from_s3(self) -> dict[str, Any]:
        import boto3

        client = boto3.client("s3", region_name=self.settings.aws_region)
        response = client.get_object(
            Bucket=self.settings.raw_s3_bucket,
            Key=self.settings.usage_summary_s3_key,
        )
        body = response["Body"].read().decode("utf-8")
        return json.loads(body)


def build_company_usage_summary(processinfo_root: str | Path) -> dict[str, Any]:
    root = Path(processinfo_root)
    aggregates: dict[str, CompanyUsageAccumulator] = {}
    source_files = 0
    source_rows = 0
    rows_with_company_name = 0

    for path in sorted(root.rglob("records.jsonl")):
        source_files += 1
        for item in _iter_dynamodb_json_lines(path):
            source_rows += 1
            company_name = _string(item, "CompanyName")
            if not company_name:
                continue
            rows_with_company_name += 1
            company_key = normalize_company_name(company_name)
            if not company_key:
                continue
            aggregates.setdefault(company_key, CompanyUsageAccumulator()).add(item)

    companies = {
        company_key: accumulator.to_summary(company_key)
        for company_key, accumulator in sorted(aggregates.items())
    }
    return {
        "meta": {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "ProcessInfo DynamoDB JSON export",
            "source_root": str(root),
            "source_files": source_files,
            "source_rows": source_rows,
            "rows_with_company_name": rows_with_company_name,
            "company_count": len(companies),
        },
        "companies": companies,
    }


def write_company_usage_summary(summary: dict[str, Any], output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _iter_dynamodb_json_lines(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            raw_item = json.loads(line)
            if isinstance(raw_item, dict):
                yield raw_item


def _string(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, dict):
        return ""
    raw = value.get("S")
    return raw.strip() if isinstance(raw, str) else ""


def _number(item: dict[str, Any], key: str) -> int:
    value = item.get(key)
    if not isinstance(value, dict):
        return 0
    raw = value.get("N")
    if raw is None:
        return 0
    try:
        return int(float(str(raw)))
    except ValueError:
        return 0


def _number_or_string(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, dict):
        return ""
    raw = value.get("N", value.get("S", ""))
    return str(raw).strip()


def _split_multi_value(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()] if value else []


def _find_fuzzy_company_candidates(
    query_key: str,
    summaries: Iterable[dict[str, Any]],
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    if not query_key:
        return []
    scored: list[dict[str, Any]] = []
    for summary in summaries:
        keys = [str(summary.get("company_key") or "")]
        for name in summary.get("company_names") or []:
            keys.append(normalize_company_name(str(name)))
        score = max((_company_match_score(query_key, key) for key in keys if key), default=0.0)
        if score >= 0.45:
            scored.append(
                {
                    "company_name": summary.get("company_name") or summary.get("company_key") or "",
                    "company_key": summary.get("company_key") or "",
                    "license_ids": summary.get("license_ids") or [],
                    "score": round(score, 4),
                    "summary": summary,
                }
            )
    scored.sort(key=lambda item: (-float(item["score"]), str(item["company_name"])))
    return scored[:limit]


def _company_match_score(query_key: str, candidate_key: str) -> float:
    if not query_key or not candidate_key:
        return 0.0
    if query_key == candidate_key:
        return 1.0
    if query_key in candidate_key or candidate_key in query_key:
        length_ratio = min(len(query_key), len(candidate_key)) / max(len(query_key), len(candidate_key))
        return 0.84 + (0.14 * length_ratio)
    query_tokens = set(query_key.split())
    candidate_tokens = set(candidate_key.split())
    token_overlap = len(query_tokens & candidate_tokens) / max(len(query_tokens | candidate_tokens), 1)
    sequence_score = SequenceMatcher(None, query_key, candidate_key).ratio()
    return (0.58 * sequence_score) + (0.42 * token_overlap)


def _single_confident_candidate(candidates: list[dict[str, Any]]) -> bool:
    if not candidates:
        return False
    top = float(candidates[0].get("score") or 0)
    second = float(candidates[1].get("score") or 0) if len(candidates) > 1 else 0.0
    return top >= 0.88 and (top - second) >= 0.06
