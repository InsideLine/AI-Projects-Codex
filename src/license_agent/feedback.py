from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .models import FeedbackEvent


class JsonFeedbackStore:
    """Append-only local feedback store for development.

    Production should write these labels to Aurora and include report/run IDs.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, event: FeedbackEvent) -> None:
        payload = asdict(event)
        payload["created_at"] = event.created_at.isoformat()
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def record(
        self,
        report_subject: str,
        finding_code: str,
        accepted: bool,
        analyst: str,
        comment: str,
    ) -> FeedbackEvent:
        event = FeedbackEvent(
            report_subject=report_subject,
            finding_code=finding_code,
            accepted=accepted,
            analyst=analyst,
            comment=comment,
            created_at=datetime.utcnow(),
        )
        self.add(event)
        return event

