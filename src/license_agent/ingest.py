from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from .settings import LicenseAgentSettings


class IngestionError(RuntimeError):
    pass


@dataclass(frozen=True)
class RawBatch:
    source_system: str
    dataset: str
    records: tuple[dict[str, Any], ...]
    extracted_at: datetime | None = None
    source_account: str | None = None
    schema_version: str | None = None
    cursor: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class PersistedBatch:
    batch_id: str
    source_system: str
    dataset: str
    record_count: int
    manifest_path: str
    records_path: str
    sha256_hex: str
    received_at: datetime


class FilesystemLandingZone:
    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def persist(self, batch: RawBatch) -> PersistedBatch:
        if not batch.source_system.strip():
            raise IngestionError("source_system is required.")
        if not batch.dataset.strip():
            raise IngestionError("dataset is required.")

        received_at = datetime.now(timezone.utc)
        batch_id = uuid4().hex
        source_slug = _slugify(batch.source_system)
        dataset_slug = _slugify(batch.dataset)
        day_prefix = received_at.strftime("%Y/%m/%d")

        target_dir = self.root_dir / source_slug / dataset_slug / day_prefix / batch_id
        target_dir.mkdir(parents=True, exist_ok=True)

        records_path = target_dir / "records.jsonl"
        manifest_path = target_dir / "manifest.json"

        digest = sha256()
        with records_path.open("w", encoding="utf-8") as handle:
            for record in batch.records:
                line = json.dumps(record, sort_keys=True)
                handle.write(line + "\n")
                digest.update(line.encode("utf-8"))
                digest.update(b"\n")

        manifest = {
            "batch_id": batch_id,
            "source_system": batch.source_system,
            "dataset": batch.dataset,
            "record_count": len(batch.records),
            "received_at": received_at.isoformat(),
            "extracted_at": batch.extracted_at.isoformat() if batch.extracted_at else None,
            "source_account": batch.source_account,
            "schema_version": batch.schema_version,
            "cursor": batch.cursor,
            "notes": batch.notes,
            "records_path": str(records_path),
            "sha256_hex": digest.hexdigest(),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

        return PersistedBatch(
            batch_id=batch_id,
            source_system=batch.source_system,
            dataset=batch.dataset,
            record_count=len(batch.records),
            manifest_path=str(manifest_path),
            records_path=str(records_path),
            sha256_hex=digest.hexdigest(),
            received_at=received_at,
        )

    def health(self) -> dict[str, object]:
        return {
            "root_dir": str(self.root_dir),
            "exists": self.root_dir.exists(),
            "writable": self.root_dir.is_dir(),
        }


def build_landing_zone(settings: LicenseAgentSettings) -> FilesystemLandingZone:
    return FilesystemLandingZone(settings.ingest_raw_root)


def storage_recommendation(settings: LicenseAgentSettings) -> dict[str, object]:
    return {
        "recommended_primary_store": "s3-athena-glue",
        "reason": (
            "The workload is analytical, not urgent, and can tolerate batch-oriented reads. "
            "That favors cheap object storage and serverless SQL over a continuously running relational cluster."
        ),
        "configured_ingest_raw_root": settings.ingest_raw_root,
        "configured_raw_s3_bucket": settings.raw_s3_bucket,
        "configured_glue_database": settings.glue_database_name,
        "configured_athena_output_s3_uri": settings.athena_output_s3_uri,
        "configured_aurora_database_url": bool(settings.aurora_database_url),
    }


def _slugify(value: str) -> str:
    collapsed = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = collapsed.strip("-_.").lower()
    if not cleaned:
        raise IngestionError("Unable to derive a safe storage path from value.")
    return cleaned
