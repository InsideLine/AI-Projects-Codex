from __future__ import annotations

import re

from .agent import LicenseViolationAgent
from .models import InvestigationInput

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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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

