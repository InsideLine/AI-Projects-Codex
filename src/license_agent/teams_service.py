from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent import LicenseViolationAgent
from .chat_store import ChatStore
from .data_query import DataQueryService, looks_like_data_query
from .feedback import JsonFeedbackStore
from .models import InvestigationInput
from .settings import LicenseAgentSettings


@dataclass(frozen=True)
class ParsedIntent:
    kind: str
    subject_type: str | None = None
    subject_value: str | None = None
    finding_code: str | None = None
    accepted: bool | None = None
    comment: str = ""
    job_id: str | None = None
    question: str = ""


class TeamsChatService:
    """Local-first Teams bot scaffold with queued jobs and structured user memory."""

    def __init__(
        self,
        settings: LicenseAgentSettings,
        *,
        agent: LicenseViolationAgent | None = None,
        store: ChatStore | None = None,
        feedback_store: JsonFeedbackStore | None = None,
        data_query_service: DataQueryService | None = None,
        run_async: bool = True,
    ) -> None:
        self.settings = settings
        self.agent = agent or LicenseViolationAgent()
        self.store = store or ChatStore(settings.app_db_path)
        feedback_path = Path(settings.report_output_root) / "feedback" / "teams_feedback.jsonl"
        self.feedback_store = feedback_store or JsonFeedbackStore(feedback_path)
        self.data_query_service = data_query_service or DataQueryService(settings)
        self.run_async = run_async
        self._lock = threading.Lock()

    def handle_message(self, text: str, user_email: str | None = None) -> dict[str, Any]:
        user_id = (user_email or "anonymous").strip() or "anonymous"
        stripped = text.strip()
        self.store.save_message(user_id, "user", stripped)
        last_job = self.store.last_completed_job(user_id)
        intent = parse_intent(stripped, last_job=last_job)

        if intent.kind == "feedback":
            response = self._record_feedback(user_id, intent)
        elif intent.kind == "feedback_needs_code":
            response = self._feedback_needs_code(user_id, intent)
        elif intent.kind == "job_status" and intent.job_id:
            response = self._job_status(user_id, intent.job_id)
        elif intent.kind == "history":
            response = self._history(user_id)
        elif intent.kind == "explain_last_report":
            response = self._explain_last_report(user_id)
        elif intent.kind == "follow_up_question":
            response = self._answer_follow_up(user_id, intent.question or stripped)
        elif intent.kind == "data_query":
            response = self._answer_data_query(user_id, intent.question or stripped)
        elif intent.kind == "help":
            response = self._help(user_id)
        elif intent.kind == "report_request" and intent.subject_type and intent.subject_value:
            response = self._enqueue_report(user_id, intent.subject_type, intent.subject_value)
        else:
            response = self._help(user_id)

        self.store.save_message(user_id, "assistant", str(response.get("message", "")))
        return response

    def state(self, user_email: str | None = None) -> dict[str, Any]:
        user_id = (user_email or "anonymous").strip() or "anonymous"
        summary = self.store.user_summary(user_id)
        return {
            "user_id": user_id,
            "memory": summary,
            "runtime": {
                "run_async": self.run_async,
                "warehouse_backend_connected": False,
                "report_output_root": self.settings.report_output_root,
                "db_path": self.settings.app_db_path,
                "data_query": self.data_query_service.runtime_status(),
            },
        }

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self.store.get_job(job_id)

    def _help(self, user_id: str) -> dict[str, Any]:
        top_subjects = self.store.user_summary(user_id).get("top_subjects", [])
        memory_hint = ""
        if top_subjects:
            top = top_subjects[0]
            memory_hint = (
                f" You most often ask about {top['subject_type']} `{top['subject_value']}` "
                f"({top['request_count']} time(s))."
            )
        return {
            "type": "help",
            "message": (
                "Ask for `license 66275132` or `company Hudson Housing Capital LLC` to queue a report. "
                "Use `status <job_id>` to check a queued report, `history` to see recent requests, and "
                "`feedback <finding_code> accepted|wrong <comment>` to teach the bot from a review. "
                "Natural language is fine too, like 'Can you check Hudson Housing Capital LLC?'"
                + memory_hint
            ),
            "state": self.state(user_id),
        }

    def _history(self, user_id: str) -> dict[str, Any]:
        summary = self.store.user_summary(user_id)
        recent_jobs = summary.get("recent_jobs", [])
        if not recent_jobs:
            message = "No report requests have been queued yet for this user."
        else:
            latest = recent_jobs[0]
            message = (
                f"You have {summary['total_jobs']} queued or completed report request(s). "
                f"The latest is `{latest['job_id']}` for {latest['subject_type']} `{latest['subject_value']}` "
                f"with status `{latest['status']}`."
            )
        return {"type": "history", "message": message, "state": self.state(user_id)}

    def _explain_last_report(self, user_id: str) -> dict[str, Any]:
        latest_job = self.store.last_completed_job(user_id)
        if not latest_job:
            return {
                "type": "explanation",
                "message": "I do not have a completed report yet for this user. Ask me to check a license or company first.",
                "state": self.state(user_id),
            }
        result = latest_job.get("result") or {}
        findings = result.get("findings") or []
        if not findings:
            message = (
                f"The latest report for {latest_job['subject_type']} `{latest_job['subject_value']}` "
                f"returned evaluation `{result.get('evaluation', 'unknown')}` with no findings yet."
            )
        else:
            top_findings = []
            for item in findings[:3]:
                if not isinstance(item, dict):
                    continue
                top_findings.append(f"`{item.get('code', 'unknown')}`: {item.get('title', 'Untitled finding')}")
            message = (
                f"I flagged {latest_job['subject_type']} `{latest_job['subject_value']}` because the latest report "
                f"returned `{result.get('evaluation', 'unknown')}` with {len(findings)} finding(s): "
                + "; ".join(top_findings)
                + "."
            )
        note = result.get("note")
        if note:
            message += f" {note}"
        return {
            "type": "explanation",
            "message": message,
            "job": latest_job,
            "state": self.state(user_id),
        }

    def _answer_follow_up(self, user_id: str, question: str) -> dict[str, Any]:
        latest_job = self.store.last_completed_job(user_id)
        if not latest_job:
            return self._answer_data_query(user_id, question)
        result = latest_job.get("result") or {}
        message = (
            f"I treated that as a follow-up about {latest_job['subject_type']} `{latest_job['subject_value']}`. "
            f"The current local scaffold has report memory and conversational routing, but the live warehouse-backed "
            f"question answering layer is still being connected. Right now the latest report says evaluation "
            f"`{result.get('evaluation', 'unknown')}` with {result.get('finding_count', 0)} finding(s). "
            "If you want a fresh report, ask me to check the company or license again."
        )
        return {
            "type": "follow_up",
            "message": message,
            "job": latest_job,
            "state": self.state(user_id),
        }

    def _answer_data_query(self, user_id: str, question: str) -> dict[str, Any]:
        result = self.data_query_service.answer(question)
        return {
            "type": "data_query",
            "query_kind": result.kind,
            "message": result.message,
            "evidence": result.evidence,
            "state": self.state(user_id),
        }

    def _enqueue_report(self, user_id: str, subject_type: str, subject_value: str) -> dict[str, Any]:
        payload = {"subject_type": subject_type, "subject_value": subject_value}
        job = self.store.create_job(
            user_id=user_id,
            job_type="investigation_report",
            subject_type=subject_type,
            subject_value=subject_value,
            payload=payload,
        )
        if self.run_async:
            thread = threading.Thread(target=self._process_report_job, args=(job["job_id"],), daemon=True)
            thread.start()
            message = (
                f"I queued report job `{job['job_id']}` for {subject_type} `{subject_value}`. "
                "Ask for `status {job_id}` in Teams to check progress."
            ).replace("{job_id}", job["job_id"])
        else:
            self._process_report_job(job["job_id"])
            message = (
                f"I ran report job `{job['job_id']}` for {subject_type} `{subject_value}` "
                "in local synchronous mode."
            )
        return {
            "type": "report_requested",
            "job_id": job["job_id"],
            "message": message,
            "state": self.state(user_id),
        }

    def _job_status(self, user_id: str, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if not job or job["user_id"] != user_id:
            return {
                "type": "job_status",
                "message": f"I could not find job `{job_id}` for this user.",
                "state": self.state(user_id),
            }
        message = (
            f"Job `{job_id}` for {job['subject_type']} `{job['subject_value']}` is `{job['status']}`."
        )
        if job.get("status") == "completed" and isinstance(job.get("result"), dict):
            result = job["result"]
            message += (
                f" Evaluation: {result.get('evaluation', 'unknown')}. "
                f"Findings: {result.get('finding_count', 0)}."
            )
        if job.get("status") == "failed" and job.get("error_text"):
            message += f" Error: {job['error_text']}"
        return {"type": "job_status", "message": message, "job": job, "state": self.state(user_id)}

    def _record_feedback(self, user_id: str, intent: ParsedIntent) -> dict[str, Any]:
        latest_job = self.store.last_completed_job(user_id)
        report_subject = latest_job["subject_value"] if latest_job else "unknown subject"
        accepted = bool(intent.accepted)
        comment = intent.comment or ""
        finding_code = intent.finding_code or "unknown_finding"
        analyst = user_id
        self.feedback_store.record(report_subject, finding_code, accepted, analyst, comment)
        preference_key = "accepted_finding_feedback" if accepted else "rejected_finding_feedback"
        preference_value = f"{finding_code}: {comment}".strip()
        self.store.save_preference(
            user_id,
            preference_key,
            preference_value,
            confidence=0.75,
            source="teams_feedback",
        )
        verdict = "accepted" if accepted else "marked wrong"
        return {
            "type": "feedback_recorded",
            "message": f"I recorded that finding `{finding_code}` was {verdict}.",
            "state": self.state(user_id),
        }

    def _feedback_needs_code(self, user_id: str, intent: ParsedIntent) -> dict[str, Any]:
        latest_job = self.store.last_completed_job(user_id)
        if not latest_job:
            return {
                "type": "feedback_needs_code",
                "message": "I need a completed report before I can attach that feedback to a finding.",
                "state": self.state(user_id),
            }
        result = latest_job.get("result") or {}
        findings = result.get("findings") or []
        finding_codes = [
            item.get("code")
            for item in findings
            if isinstance(item, dict) and isinstance(item.get("code"), str) and item.get("code")
        ]
        if finding_codes:
            options = ", ".join(f"`{code}`" for code in finding_codes[:5])
            message = (
                "I understood that as reviewer feedback, but I need the finding code because the latest report has "
                f"multiple possible findings. Try `feedback <finding_code> accepted|wrong ...`. Available codes: {options}."
            )
        else:
            message = "I understood that as reviewer feedback, but the latest report has no findings to attach it to."
        return {"type": "feedback_needs_code", "message": message, "state": self.state(user_id)}

    def _process_report_job(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        if not job:
            return
        self.store.mark_processing(job_id)
        try:
            subject_type = job["subject_type"]
            subject_value = job["subject_value"]
            investigation = InvestigationInput(
                license_id=subject_value if subject_type == "license" else None,
                company_name=subject_value if subject_type == "company" else None,
            )
            with self._lock:
                report = self.agent.create_report(investigation)
            findings = [
                {
                    "code": finding.code,
                    "title": finding.title,
                    "severity": finding.severity.value,
                    "detail": finding.detail,
                    "evidence": finding.evidence,
                }
                for finding in report.findings
            ]
            result = {
                "subject": report.subject,
                "evaluation": report.evaluation,
                "finding_count": len(findings),
                "findings": findings,
                "activation_count": report.activation_count,
                "usage_record_count": report.usage_record_count,
                "note": (
                    "This job path is ready for Teams-style orchestration, memory, and reviewer feedback. "
                    "The live Athena or Aurora data loader still needs to be connected so completed jobs use the "
                    "compiled warehouse instead of an empty local investigation shell."
                ),
            }
            self.store.mark_completed(job_id, result)
        except Exception as exc:  # pragma: no cover
            self.store.mark_failed(job_id, f"{type(exc).__name__}: {exc}")


def parse_intent(text: str, *, last_job: dict[str, Any] | None = None) -> ParsedIntent:
    stripped = text.strip()
    lower = stripped.lower()
    last_finding_codes = _finding_codes(last_job)
    if not stripped or lower in {"help", "?", "commands"}:
        return ParsedIntent(kind="help")
    if lower in {"history", "recent", "show history"}:
        return ParsedIntent(kind="history")

    status_match = re.search(r"\bstatus\b(?:\s+of)?\s+([A-Za-z0-9_-]+)", stripped, flags=re.IGNORECASE)
    if status_match:
        return ParsedIntent(kind="job_status", job_id=status_match.group(1))

    feedback_match = re.fullmatch(
        r"feedback\s+([A-Za-z0-9_.-]+)\s+(accepted|wrong|rejected)\s*(.*)",
        stripped,
        flags=re.IGNORECASE,
    )
    if feedback_match:
        verdict = feedback_match.group(2).lower()
        return ParsedIntent(
            kind="feedback",
            finding_code=feedback_match.group(1),
            accepted=verdict == "accepted",
            comment=feedback_match.group(3).strip(),
        )

    natural_feedback = _parse_natural_feedback(stripped, last_finding_codes=last_finding_codes)
    if natural_feedback is not None:
        return natural_feedback

    if _looks_like_follow_up_question(lower):
        return ParsedIntent(kind="follow_up_question", question=stripped)

    if _looks_like_explanation_request(lower):
        return ParsedIntent(kind="explain_last_report", question=stripped)

    if looks_like_data_query(stripped):
        return ParsedIntent(kind="data_query", question=stripped)

    company_match = re.search(r"\bcompany\s*[:#]?\s*(.+)$", stripped, flags=re.IGNORECASE)
    if company_match:
        return ParsedIntent(
            kind="report_request",
            subject_type="company",
            subject_value=company_match.group(1).strip(),
        )

    company_value = _extract_company_subject(stripped)
    if company_value:
        return ParsedIntent(kind="report_request", subject_type="company", subject_value=company_value)

    license_match = re.search(
        r"\b(?:license|lic|id)\s*[:#]?\s*([A-Za-z0-9._-]{4,})\b",
        stripped,
        flags=re.IGNORECASE,
    )
    if license_match:
        return ParsedIntent(kind="report_request", subject_type="license", subject_value=license_match.group(1))

    contextual_license_match = re.search(
        r"\b(?:report on|check|investigate|review|analyze|analyse|look into)\s+license\s+([A-Za-z0-9._-]{4,})\b",
        stripped,
        flags=re.IGNORECASE,
    )
    if contextual_license_match:
        return ParsedIntent(
            kind="report_request",
            subject_type="license",
            subject_value=contextual_license_match.group(1),
        )

    return ParsedIntent(kind="report_request", subject_type="company", subject_value=stripped)


def _parse_natural_feedback(text: str, *, last_finding_codes: list[str]) -> ParsedIntent | None:
    lowered = text.lower()
    if not any(phrase in lowered for phrase in ("that finding", "this finding", "that is wrong", "that's wrong", "that was wrong", "this is wrong", "that looks wrong", "that seems wrong", "that is correct", "that's correct", "that looks right", "that seems right")):
        return None
    accepted = True
    if "wrong" in lowered:
        accepted = False
    comment = _extract_reason_comment(text)
    if len(last_finding_codes) == 1:
        return ParsedIntent(
            kind="feedback",
            finding_code=last_finding_codes[0],
            accepted=accepted,
            comment=comment,
        )
    return ParsedIntent(kind="feedback_needs_code", accepted=accepted, comment=comment)


def _extract_reason_comment(text: str) -> str:
    because_match = re.search(r"\bbecause\b(.+)$", text, flags=re.IGNORECASE)
    if because_match:
        return because_match.group(1).strip().rstrip(".")
    return text.strip().rstrip(".")


def _looks_like_explanation_request(lower: str) -> bool:
    patterns = (
        "why did you flag",
        "why was this flagged",
        "why is this suspicious",
        "why is that suspicious",
        "why did you mark",
        "what did you find",
        "explain the findings",
        "explain this report",
        "why do you think",
    )
    return any(pattern in lower for pattern in patterns)


def _looks_like_follow_up_question(lower: str) -> bool:
    patterns = (
        "have we seen this company",
        "have we seen this license",
        "does this look",
        "is this violating",
        "is that violating",
        "what about this company",
        "what about this license",
    )
    return any(pattern in lower for pattern in patterns)


def _extract_company_subject(text: str) -> str | None:
    patterns = (
        r"(?:can you|could you|please)\s+(?:check|investigate|review|analyze|analyse|look into)\s+(?:whether\s+)?(.+?)(?:\s+might be violating(?: their license)?|\s+may be violating(?: their license)?|\s+is violating(?: their license)?|[?.!]*)$",
        r"(?:give me|create|build|pull)\s+(?:a\s+)?report\s+(?:for|on)\s+(.+?)(?:[?.!]*)$",
        r"(?:check|investigate|review|analyze|analyse|look into|report on)\s+(.+?)(?:[?.!]*)$",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = _clean_company_subject(match.group(1))
        if value:
            return value
    return None


def _clean_company_subject(value: str) -> str:
    cleaned = re.sub(r"^(the\s+company\s+|company\s+)", "", value.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\s+(might be violating(?: their license)?|may be violating(?: their license)?|is violating(?: their license)?)\s*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\s+(for|about)\s+(possible\s+)?license\s+violations?.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.strip(" .?!,;:")
    return cleaned


def _finding_codes(last_job: dict[str, Any] | None) -> list[str]:
    if not last_job:
        return []
    result = last_job.get("result")
    if not isinstance(result, dict):
        return []
    findings = result.get("findings")
    if not isinstance(findings, list):
        return []
    return [
        item.get("code")
        for item in findings
        if isinstance(item, dict) and isinstance(item.get("code"), str) and item.get("code")
    ]
