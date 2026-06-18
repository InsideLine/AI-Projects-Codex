from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .agent import LicenseViolationAgent
from .ingest import RawBatch, build_landing_zone, storage_recommendation
from .models import InvestigationInput
from .settings import safe_load_settings
from .solo import SoloClient
from .zoho import ZohoClient, ZohoError

try:
    from fastapi import FastAPI
    from pydantic import BaseModel
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install the api extra: python -m pip install -e '.[api]'") from exc


app = FastAPI(title="License Violation Data Analyzer Agent")
agent = LicenseViolationAgent()


class TeamsMessage(BaseModel):
    text: str
    user_email: str | None = None


class RawIngestRequest(BaseModel):
    source_system: str
    dataset: str
    records: list[dict[str, Any]]
    extracted_at: datetime | None = None
    source_account: str | None = None
    schema_version: str | None = None
    cursor: str | None = None
    notes: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/aws/health")
def aws_health() -> dict[str, object]:
    settings, warning = safe_load_settings(".env")
    payload = settings.aws_cli_status()
    payload["warning"] = warning
    return payload


@app.get("/ingest/health")
def ingest_health() -> dict[str, object]:
    settings, warning = safe_load_settings(".env")
    landing_zone = build_landing_zone(settings)
    payload = {
        **settings.ingest_status(),
        "landing_zone": landing_zone.health(),
        "recommendation": storage_recommendation(settings),
        "warning": warning,
    }
    return payload


@app.post("/ingest/raw-batch")
def ingest_raw_batch(batch: RawIngestRequest) -> dict[str, object]:
    settings, warning = safe_load_settings(".env")
    landing_zone = build_landing_zone(settings)
    persisted = landing_zone.persist(
        RawBatch(
            source_system=batch.source_system,
            dataset=batch.dataset,
            records=tuple(batch.records),
            extracted_at=batch.extracted_at,
            source_account=batch.source_account,
            schema_version=batch.schema_version,
            cursor=batch.cursor,
            notes=batch.notes,
        )
    )
    return {
        "batch_id": persisted.batch_id,
        "source_system": persisted.source_system,
        "dataset": persisted.dataset,
        "record_count": persisted.record_count,
        "manifest_path": persisted.manifest_path,
        "records_path": persisted.records_path,
        "sha256_hex": persisted.sha256_hex,
        "received_at": persisted.received_at.isoformat(),
        "warning": warning,
    }


@app.get("/zoho/health")
def zoho_health() -> dict[str, object]:
    settings, warning = safe_load_settings(".env")
    client = ZohoClient(settings)
    payload = {
        **settings.zoho_status(),
        **client.status().__dict__,
    }
    payload["warning"] = warning
    return payload


@app.get("/solo/health")
def solo_health() -> dict[str, object]:
    settings, warning = safe_load_settings(".env")
    client = SoloClient(settings)
    payload = client.status()
    payload["warning"] = warning
    return payload


@app.get("/zoho/oauth/url")
def zoho_oauth_url() -> dict[str, str]:
    settings, _ = safe_load_settings(".env")
    client = ZohoClient(settings)
    try:
        return {"authorization_url": client.build_authorization_url()}
    except ZohoError as exc:
        return {"error": str(exc)}


@app.post("/teams/message")
def teams_message(message: TeamsMessage) -> dict[str, object]:
    query = parse_subject(message.text)
    report = agent.create_report(InvestigationInput(**query))
    return {
        "subject": report.subject,
        "evaluation": report.evaluation,
        "finding_count": len(report.findings),
        "findings": [
            {
                "code": finding.code,
                "title": finding.title,
                "severity": finding.severity.value,
                "detail": finding.detail,
                "evidence": finding.evidence,
            }
            for finding in report.findings
        ],
    }


def parse_subject(text: str) -> dict[str, str]:
    license_match = re.search(r"\b(?:license|lic|id)\s*[:#]?\s*([A-Za-z0-9._-]{4,})\b", text, flags=re.IGNORECASE)
    if license_match:
        return {"license_id": license_match.group(1)}
    company_match = re.search(r"\bcompany\s*[:#]?\s*(.+)$", text, flags=re.IGNORECASE)
    if company_match:
        return {"company_name": company_match.group(1).strip()}
    return {"company_name": text.strip()}
