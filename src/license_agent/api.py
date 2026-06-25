from __future__ import annotations

import re
import threading
from datetime import datetime
from functools import lru_cache
from typing import Any

from .agent import LicenseViolationAgent
from .aws_chat_store import DynamoDbChatStore
from .bot_framework import (
    BotFrameworkActivityHandler,
    BotFrameworkAuthError,
    BotFrameworkReplyError,
    is_bot_framework_activity,
    load_bot_framework_credentials,
)
from .chat_store import ChatStore
from .ingest import RawBatch, build_landing_zone, storage_recommendation
from .models import InvestigationInput
from .settings import safe_load_settings
from .solo import SoloClient
from .teams_service import TeamsChatService
from .zoho import ZohoClient, ZohoError

try:
    from fastapi import FastAPI, HTTPException, Request
    from pydantic import BaseModel
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("Install the api extra: python -m pip install -e '.[api]'") from exc


app = FastAPI(title="License Violation Data Analyzer Agent")
agent = LicenseViolationAgent()
_teams_lock = threading.Lock()


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


@lru_cache(maxsize=1)
def get_teams_service() -> TeamsChatService:
    settings, _ = safe_load_settings(".env")
    return TeamsChatService(settings, agent=agent, store=build_chat_store(settings))


@lru_cache(maxsize=1)
def get_bot_framework_handler() -> BotFrameworkActivityHandler:
    settings, _ = safe_load_settings(".env")
    return BotFrameworkActivityHandler(credentials=load_bot_framework_credentials(settings))


def build_chat_store(settings):
    backend = settings.chat_store_backend.strip().lower()
    if backend == "dynamodb":
        if not settings.chat_state_table_name:
            raise RuntimeError("CHAT_STATE_TABLE_NAME is required when CHAT_STORE_BACKEND=dynamodb.")
        return DynamoDbChatStore(settings.chat_state_table_name)
    return ChatStore(settings.app_db_path)


def require_shared_secret(request: Request) -> None:
    settings, _ = safe_load_settings(".env")
    if not settings.teams_shared_secret:
        return
    provided = request.headers.get("x-license-agent-secret", "")
    if provided != settings.teams_shared_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")


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
def ingest_raw_batch(batch: RawIngestRequest, request: Request) -> dict[str, object]:
    require_shared_secret(request)
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
async def teams_message(request: Request) -> dict[str, object]:
    payload = await request.json()
    if is_bot_framework_activity(payload):
        try:
            with _teams_lock:
                return get_bot_framework_handler().handle(
                    activity=payload,
                    authorization_header=request.headers.get("authorization"),
                    teams_service=get_teams_service(),
                )
        except BotFrameworkAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except BotFrameworkReplyError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    require_shared_secret(request)
    message = TeamsMessage.model_validate(payload)
    with _teams_lock:
        return get_teams_service().handle_message(message.text, message.user_email)


@app.get("/teams/state")
def teams_state(request: Request, user_email: str | None = None) -> dict[str, object]:
    require_shared_secret(request)
    with _teams_lock:
        return get_teams_service().state(user_email)


@app.get("/teams/jobs/{job_id}")
def teams_job(job_id: str, request: Request) -> dict[str, object]:
    require_shared_secret(request)
    with _teams_lock:
        job = get_teams_service().get_job(job_id)
    return {"job": job, "found": job is not None}


def parse_subject(text: str) -> dict[str, str]:
    license_match = re.search(r"\b(?:license|lic|id)\s*[:#]?\s*([A-Za-z0-9._-]{4,})\b", text, flags=re.IGNORECASE)
    if license_match:
        return {"license_id": license_match.group(1)}
    company_match = re.search(r"\bcompany\s*[:#]?\s*(.+)$", text, flags=re.IGNORECASE)
    if company_match:
        return {"company_name": company_match.group(1).strip()}
    return {"company_name": text.strip()}
