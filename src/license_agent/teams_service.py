from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent import LicenseViolationAgent
from .chat_store import ChatStore
from .correlation_analysis import normalize_company_name
from .data_query import DataQueryService, looks_like_data_query
from .feedback import JsonFeedbackStore
from .models import InvestigationInput
from .report_artifacts import publish_word_report
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
        clarification = self._resolve_pending_company_selection(user_id, stripped)
        if clarification:
            response = self._enqueue_report(user_id, "company", clarification)
            self.store.save_message(user_id, "assistant", str(response.get("message", "")))
            return response
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
        data_query_status = self.data_query_service.runtime_status()
        usage_summary_status = data_query_status.get("aws_usage_summary") or {}
        return {
            "user_id": user_id,
            "memory": summary,
            "runtime": {
                "run_async": self.run_async,
                "warehouse_backend_connected": bool(usage_summary_status.get("configured")),
                "report_output_root": self.settings.report_output_root,
                "db_path": self.settings.app_db_path,
                "data_query": data_query_status,
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
        if subject_type == "company":
            clarification = self._company_clarification_response(user_id, subject_value)
            if clarification is not None:
                return clarification
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
                f"I started report job {job['job_id']} for {subject_type} {subject_value}. "
                "I am gathering the available license, usage, and CRM context now."
            ).replace("{job_id}", job["job_id"])
        else:
            self._process_report_job(job["job_id"])
            completed_job = self.store.get_job(job["job_id"]) or job
            message = self._completed_report_message(completed_job)
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
            message = self._completed_report_message(job)
        if job.get("status") == "failed" and job.get("error_text"):
            message += f" Error: {job['error_text']}"
        return {"type": "job_status", "message": message, "job": job, "state": self.state(user_id)}

    def _company_clarification_response(self, user_id: str, subject_value: str) -> dict[str, Any] | None:
        usage_client = getattr(self.data_query_service, "usage_client", None)
        if usage_client is None or not hasattr(usage_client, "find_company"):
            return None
        lookup = usage_client.find_company(subject_value)
        if lookup.get("error") or lookup.get("match"):
            return None
        candidates = [
            item for item in (lookup.get("candidates") or [])
            if isinstance(item, dict) and item.get("company_name")
        ]
        if not candidates:
            return None
        self._save_pending_company_candidates(user_id, subject_value, candidates)
        options = []
        for index, item in enumerate(candidates[:5], start=1):
            licenses = item.get("license_ids") or []
            license_hint = f"; licenses: {', '.join(str(value) for value in licenses[:3])}" if licenses else ""
            options.append(f"{index}. {item['company_name']} (match {float(item.get('score') or 0):.0%}{license_hint})")
        message = (
            f"I found multiple possible company matches for \"{subject_value}\". "
            "Which one should I use for the report?\n\n"
            + "\n".join(options)
            + "\n\nReply with the number, the company name, or give me a license ID."
        )
        return {
            "type": "company_clarification",
            "message": message,
            "requested_subject": subject_value,
            "candidates": candidates[:5],
            "state": self.state(user_id),
        }

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
            usage_result = self._build_usage_report_result(subject_type, subject_value)
            if usage_result is not None:
                usage_result = self._finalize_report_result(job_id, usage_result)
                self.store.mark_completed(job_id, usage_result)
                return

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
                "data_connected": bool(report.activation_count or report.usage_record_count),
                "note": (
                    "This job path is ready for Teams-style orchestration, memory, and reviewer feedback. "
                    "The live Athena or Aurora data loader still needs to be connected so completed jobs use the "
                    "compiled warehouse instead of an empty local investigation shell."
                ),
            }
            result = self._finalize_report_result(job_id, result)
            self.store.mark_completed(job_id, result)
        except Exception as exc:  # pragma: no cover
            self.store.mark_failed(job_id, f"{type(exc).__name__}: {exc}")

    def _build_usage_report_result(self, subject_type: str, subject_value: str) -> dict[str, Any] | None:
        usage_client = getattr(self.data_query_service, "usage_client", None)
        if usage_client is None:
            return None

        if subject_type == "license" and hasattr(usage_client, "find_license"):
            lookup = usage_client.find_license(subject_value)
        elif subject_type == "company" and hasattr(usage_client, "find_company"):
            lookup = usage_client.find_company(subject_value)
        else:
            return None

        if lookup.get("error"):
            raise RuntimeError(f"Usage summary lookup failed: {lookup['error']}")
        summary = lookup.get("match")
        if not summary:
            return None
        solo_context = self._build_solo_context(subject_type, subject_value, summary)
        return build_usage_report_result(
            subject_type,
            subject_value,
            summary,
            lookup.get("summary_meta") or {},
            crm_context=self._build_crm_context(subject_type, subject_value, summary),
            solo_context=solo_context,
            ip_geolocation_context=self._build_ip_geolocation_context(summary, solo_context=solo_context),
        )

    def _build_crm_context(self, subject_type: str, subject_value: str, summary: dict[str, Any]) -> dict[str, Any]:
        aurora_client = getattr(self.data_query_service, "aurora_client", None)
        if aurora_client is None:
            return {"configured": False, "error": "", "status": {}}
        status = aurora_client.status()
        context: dict[str, Any] = {"configured": bool(status.get("configured")), "error": "", "status": status}
        if not context["configured"]:
            return context
        company_name = str(summary.get("company_name") or (subject_value if subject_type == "company" else "")).strip()
        license_text = subject_value if subject_type == "license" else None
        usage_license_ids = [str(value).strip() for value in summary.get("license_ids") or [] if str(value).strip()]
        try:
            if company_name:
                context["account_lookup"] = aurora_client.search_company(company_name, limit=3)
            license_lookup = aurora_client.search_active_linktek_licenses(
                company_name=company_name or None,
                license_text=license_text,
                limit=10,
            )
            lookup_scope = "active"
            if not (license_lookup.get("rows") or []) and license_text is None:
                for usage_license_id in usage_license_ids:
                    license_lookup = aurora_client.search_active_linktek_licenses(
                        company_name=None,
                        license_text=usage_license_id,
                        limit=10,
                    )
                    if license_lookup.get("rows"):
                        lookup_scope = "active_by_usage_license"
                        break
            if not (license_lookup.get("rows") or []) and hasattr(aurora_client, "search_linktek_licenses"):
                license_lookup = aurora_client.search_linktek_licenses(
                    company_name=company_name or None,
                    license_text=license_text,
                    limit=10,
                    active_only=False,
                )
                lookup_scope = "historical"
            if not (license_lookup.get("rows") or []) and license_text is None and hasattr(aurora_client, "search_linktek_licenses"):
                for usage_license_id in usage_license_ids:
                    license_lookup = aurora_client.search_linktek_licenses(
                        company_name=None,
                        license_text=usage_license_id,
                        limit=10,
                        active_only=False,
                    )
                    if license_lookup.get("rows"):
                        lookup_scope = "historical_by_usage_license"
                        break
            license_lookup["lookup_scope"] = lookup_scope
            context["license_lookup"] = license_lookup
            licenses = license_lookup.get("rows") or []
            if licenses:
                context["linked_records"] = aurora_client.linked_records_for_active_licenses(licenses, per_table_limit=5)
        except Exception as exc:  # pragma: no cover - live Aurora
            context["error"] = f"{type(exc).__name__}: {exc}"
        return context

    def _build_solo_context(self, subject_type: str, subject_value: str, summary: dict[str, Any]) -> dict[str, Any]:
        company_name = str(summary.get("company_name") or (subject_value if subject_type == "company" else "")).strip()
        usage_license_ids = [str(value).strip() for value in summary.get("license_ids") or [] if str(value).strip()]
        if not company_name and not usage_license_ids:
            return {"configured": False, "error": "", "metrics": None}
        try:
            metrics = None
            if company_name:
                company_key = normalize_company_name(company_name)
                metrics = self.data_query_service._company_metrics(company_key)
            if metrics is None and hasattr(self.data_query_service, "company_metrics_for_license_ids"):
                metrics = self.data_query_service.company_metrics_for_license_ids(usage_license_ids)
        except Exception as exc:
            return {"configured": False, "error": f"{type(exc).__name__}: {exc}", "metrics": None}
        if metrics is None:
            return {"configured": False, "error": "", "metrics": None}
        return {"configured": True, "error": "", "metrics": metrics.__dict__}

    def _build_ip_geolocation_context(
        self,
        summary: dict[str, Any],
        *,
        solo_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = getattr(self.data_query_service, "ip_geolocation_client", None)
        if client is None:
            return {"configured": False, "error": "", "records": {}}
        public_ips = [str(value) for value in summary.get("public_ips") or [] if str(value).strip()]
        metrics = (solo_context or {}).get("metrics") or {}
        activation_ips = [str(value) for value in metrics.get("activation_ips") or [] if str(value).strip()]
        requested_ips = sorted(set(public_ips + activation_ips))
        if not requested_ips:
            return {"configured": False, "error": "", "records": {}, "public_ips": [], "activation_ips": []}
        result = client.lookup_many(requested_ips)
        result["public_ips"] = public_ips
        result["activation_ips"] = activation_ips
        return result

    def _finalize_report_result(self, job_id: str, result: dict[str, Any]) -> dict[str, Any]:
        report_document = result.get("report_document")
        if not isinstance(report_document, dict):
            return result
        try:
            artifact = publish_word_report(self.settings, job_id=job_id, report_document=report_document)
        except Exception as exc:  # pragma: no cover - live S3
            result["artifact_error"] = f"{type(exc).__name__}: {exc}"
            return result
        if artifact:
            result["word_report"] = artifact
        return result

    def _completed_report_message(self, job: dict[str, Any]) -> str:
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        report_text = str(result.get("report_text") or "").strip()
        if not report_text:
            if result.get("data_connected") is False:
                report_text = (
                    "I completed the report shell, but I could not verify this company/license against connected "
                    "activation, usage, or CRM data yet. Treat this as not enough connected data, not as a clean result."
                )
            else:
                report_text = (
                    f"Evaluation: {result.get('evaluation', 'unknown')}. "
                    f"Findings: {result.get('finding_count', 0)}."
                )
        message = f"Completed report job {job['job_id']}.\n\n{report_text}"
        word_report = result.get("word_report") if isinstance(result, dict) else None
        if isinstance(word_report, dict) and word_report.get("url"):
            message += f"\n\n**Word report:** [Download DOCX]({word_report['url']})"
        artifact_error = result.get("artifact_error") if isinstance(result, dict) else None
        if artifact_error:
            message += f"\n\nWord report could not be created yet: {artifact_error}"
        return message

    def _save_pending_company_candidates(
        self,
        user_id: str,
        requested_subject: str,
        candidates: list[dict[str, Any]],
    ) -> None:
        payload = {
            "requested_subject": requested_subject,
            "candidates": [
                {
                    "company_name": item.get("company_name"),
                    "company_key": item.get("company_key"),
                    "license_ids": item.get("license_ids") or [],
                    "score": item.get("score"),
                }
                for item in candidates[:5]
            ],
        }
        self.store.save_preference(
            user_id,
            "pending_company_clarification",
            json.dumps(payload, sort_keys=True),
            confidence=1.0,
            source="company_search",
        )

    def _resolve_pending_company_selection(self, user_id: str, text: str) -> str | None:
        pending = self._latest_pending_company_candidates(user_id)
        if not pending:
            return None
        candidates = pending.get("candidates") or []
        if not isinstance(candidates, list) or not candidates:
            return None
        cleaned = text.strip()
        if not cleaned:
            return None
        index = _selection_index(cleaned)
        if index is not None and 0 <= index < len(candidates):
            company_name = candidates[index].get("company_name")
            return str(company_name) if company_name else None
        cleaned_key = normalize_company_name(cleaned)
        if not cleaned_key:
            return None
        for candidate in candidates:
            candidate_name = str(candidate.get("company_name") or "")
            candidate_key = normalize_company_name(candidate_name)
            if cleaned_key == candidate_key or cleaned_key in candidate_key or candidate_key in cleaned_key:
                return candidate_name
        return None

    def _latest_pending_company_candidates(self, user_id: str) -> dict[str, Any] | None:
        for preference in self.store.get_preferences(user_id, limit=10):
            if preference.get("preference_key") != "pending_company_clarification":
                continue
            try:
                payload = json.loads(str(preference.get("preference_value") or "{}"))
            except json.JSONDecodeError:
                return None
            return payload if isinstance(payload, dict) else None
        return None


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

    company_match = re.search(r"^\s*company\s*[:#]?\s+(.+)$", stripped, flags=re.IGNORECASE)
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
    cleaned = re.split(
        r"(?:\.\s+|\?\s+|!\s+)(?:i\s+am|i'm|we\s+are|we're|looking|please)\b",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    cleaned = re.split(
        r"\s+\b(?:i\s+am|i'm|we\s+are|we're)\s+looking\b",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
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


def build_usage_report_result(
    subject_type: str,
    requested_subject: str,
    summary: dict[str, Any],
    summary_meta: dict[str, Any],
    *,
    crm_context: dict[str, Any] | None = None,
    solo_context: dict[str, Any] | None = None,
    ip_geolocation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    crm_context = crm_context or {}
    solo_context = solo_context or {}
    ip_geolocation_context = ip_geolocation_context or {}
    findings = _usage_report_findings(
        summary,
        crm_context=crm_context,
        solo_context=solo_context,
        ip_geolocation_context=ip_geolocation_context,
    )
    evaluation = _usage_report_evaluation(findings)
    report_document = _usage_report_document(
        requested_subject=requested_subject,
        subject_type=subject_type,
        summary=summary,
        findings=findings,
        evaluation=evaluation,
        summary_meta=summary_meta,
        crm_context=crm_context,
        solo_context=solo_context,
        ip_geolocation_context=ip_geolocation_context,
    )
    report_text = _usage_report_text(report_document=report_document)
    return {
        "subject": summary.get("company_name") or requested_subject,
        "requested_subject": requested_subject,
        "subject_type": subject_type,
        "evaluation": evaluation,
        "finding_count": len(findings),
        "findings": findings,
        "activation_count": 0,
        "usage_record_count": int(summary.get("run_count") or 0),
        "total_links_processed": int(summary.get("links_processed") or 0),
        "total_files_processed": int(summary.get("files_processed") or 0),
        "total_file_size_bytes": int(summary.get("file_size_in_bytes") or 0),
        "data_connected": True,
        "data_sources": {
            "aws_processinfo_summary": True,
            "solo_activations": bool(solo_context.get("configured")),
            "crm_entitlements": bool(crm_context.get("configured")),
            "ip_geolocation": bool(ip_geolocation_context.get("records")),
        },
        "crm_context": crm_context,
        "solo_context": solo_context,
        "ip_geolocation_context": ip_geolocation_context,
        "usage_summary": summary,
        "summary_meta": summary_meta,
        "report_document": report_document,
        "report_text": report_text,
        "note": (
            "This report uses the connected AWS ProcessInfo usage summary plus any connected CRM, SOLO, and IP "
            "geolocation context available at report time."
        ),
    }


def _usage_report_findings(
    summary: dict[str, Any],
    *,
    crm_context: dict[str, Any],
    solo_context: dict[str, Any],
    ip_geolocation_context: dict[str, Any],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    machine_count = int(summary.get("machine_count") or 0)
    mac_count = int(summary.get("mac_count") or 0)
    public_ip_count = int(summary.get("public_ip_count") or 0)
    file_size_gib = float(summary.get("file_size_gib") or 0)
    files_processed = int(summary.get("files_processed") or 0)
    links_processed = int(summary.get("links_processed") or 0)
    personnel_signal = _licensed_personnel_signal(crm_context=crm_context, solo_context=solo_context)

    if machine_count > 1:
        findings.append(
            {
                "code": "multiple_machine_names",
                "title": "License usage appears on multiple machine names",
                "severity": "medium",
                "detail": f"ProcessInfo shows {machine_count} machine names for this company/license.",
                "evidence": {"machine_names": summary.get("machine_names") or []},
            }
        )
    if mac_count > 1:
        findings.append(
            {
                "code": "multiple_mac_addresses",
                "title": "License usage appears on multiple MAC addresses",
                "severity": "medium",
                "detail": f"ProcessInfo shows {mac_count} MAC addresses for this company/license.",
                "evidence": {"mac_addresses": summary.get("mac_addresses") or []},
            }
        )
    if public_ip_count > 1:
        ip_records = ip_geolocation_context.get("records") or {}
        if ip_records:
            ip_detail = (
                f"ProcessInfo shows {public_ip_count} public IP addresses. GeoLite2 locations are listed in the "
                "IP Geolocation Evidence section for comparison against the organization definition."
            )
        else:
            ip_detail = (
                f"ProcessInfo shows {public_ip_count} public IP addresses. This needs geolocation before it can "
                "be treated as an organization-definition issue."
            )
        findings.append(
            {
                "code": "multiple_public_ips",
                "title": "Usage appears from multiple public IP addresses",
                "severity": "low",
                "detail": ip_detail,
                "evidence": {"public_ips": summary.get("public_ips") or []},
            }
        )
    if personnel_signal.get("value"):
        personnel = float(personnel_signal["value"])
        gib_per_person = file_size_gib / personnel if personnel else 0
        if gib_per_person >= 100:
            findings.append(
                {
                    "code": "usage_over_eula_review_threshold",
                    "title": "Usage exceeds EULA review threshold",
                    "severity": "high",
                    "detail": (
                        f"ProcessInfo shows about {gib_per_person:,.2f} GiB per licensed person "
                        f"({file_size_gib:,.2f} GiB / {personnel_signal['display_value']}), above the 100 GiB "
                        "per licensed-person review threshold."
                    ),
                    "evidence": {
                        "file_size_gib": file_size_gib,
                        "licensed_personnel": personnel_signal["value"],
                        "licensed_personnel_source": personnel_signal.get("source"),
                        "gib_per_licensed_person": gib_per_person,
                        "files_processed": files_processed,
                        "links_processed": links_processed,
                    },
                }
            )
    elif file_size_gib >= 100:
        findings.append(
            {
                "code": "high_total_file_volume",
                "title": "High total file volume",
                "severity": "medium",
                "detail": (
                    f"ProcessInfo shows about {file_size_gib:,.2f} GiB processed. The EULA 95% usage threshold "
                    "still requires personnel entitlement from CRM before calculating GB per licensed person."
                ),
                "evidence": {
                    "file_size_gib": file_size_gib,
                    "files_processed": files_processed,
                    "links_processed": links_processed,
                },
            }
        )
    if not solo_context.get("configured"):
        findings.append(
            {
                "code": "missing_solo_activation_timeline",
                "title": "SOLO activation timeline not found for this subject",
                "severity": "low",
                "detail": "This report cannot yet compare first activation date to first usage date.",
                "evidence": {},
            }
        )
    if not _has_crm_entitlement_context(crm_context):
        findings.append(
            {
                "code": "missing_crm_entitlement",
                "title": "CRM entitlement data not found for this subject",
                "severity": "medium",
                "detail": "Personnel licensed and organization definition are required before drawing EULA conclusions.",
                "evidence": {},
            }
        )
    if public_ip_count > 0 and not (ip_geolocation_context.get("records") or {}):
        findings.append(
            {
                "code": "missing_ip_geolocation",
                "title": "IP geolocation is not connected",
                "severity": "low",
                "detail": "Public IP addresses need city, region, and country enrichment before comparing usage geography to the organization definition.",
                "evidence": {"public_ips": summary.get("public_ips") or []},
            }
        )
    findings.extend(
        _automated_consistency_findings(
            summary=summary,
            crm_context=crm_context,
            solo_context=solo_context,
            ip_geolocation_context=ip_geolocation_context,
        )
    )
    return findings


def _usage_report_evaluation(findings: list[dict[str, Any]]) -> str:
    finding_codes = {str(item.get("code") or "") for item in findings}
    if "usage_over_eula_review_threshold" in finding_codes:
        return "review recommended"
    if {"multiple_machine_names", "multiple_mac_addresses"} <= finding_codes:
        return "review recommended"
    if "high_total_file_volume" in finding_codes:
        return "review recommended"
    return "usage data found; entitlement review needed"


def _usage_report_text(
    *,
    report_document: dict[str, Any],
) -> str:
    sections = report_document.get("sections") or []
    lines = [f"**{report_document.get('title', 'License Violation Review')}**"]
    subtitle = report_document.get("subtitle")
    if subtitle:
        lines.append(str(subtitle))
    for section in sections:
        if not isinstance(section, dict):
            continue
        lines.append("")
        lines.append(f"**{section.get('heading', 'Section')}**")
        for item in section.get("body") or []:
            lines.append(str(item))
        for item in section.get("bullets") or []:
            lines.append(f"- {item}")
    return "\n".join(lines)


def _usage_report_document(
    *,
    requested_subject: str,
    subject_type: str,
    summary: dict[str, Any],
    findings: list[dict[str, Any]],
    evaluation: str,
    summary_meta: dict[str, Any],
    crm_context: dict[str, Any],
    solo_context: dict[str, Any],
    ip_geolocation_context: dict[str, Any],
) -> dict[str, Any]:
    company_name = summary.get("company_name") or requested_subject
    license_ids = ", ".join(summary.get("license_ids") or []) or "unknown"
    files_processed = _format_int(summary.get("files_processed"))
    links_processed = _format_int(summary.get("links_processed"))
    run_count = _format_int(summary.get("run_count"))
    file_size_gib = float(summary.get("file_size_gib") or 0)
    machine_count = _format_int(summary.get("machine_count"))
    mac_count = _format_int(summary.get("mac_count"))
    public_ip_count_raw = int(summary.get("public_ip_count") or 0)
    public_ip_count = _format_int(public_ip_count_raw)
    first_start = summary.get("first_start_time") or "unknown"
    last_end = summary.get("last_end_time") or "unknown"
    crm_section = _crm_report_section(crm_context)
    solo_section = _solo_report_section(solo_context)
    ip_section = _ip_geolocation_report_section(ip_geolocation_context, summary.get("public_ips") or [])
    threshold_section = _eula_threshold_report_section(summary, crm_context, solo_context)
    consistency_section = _automated_consistency_report_section(
        summary=summary,
        crm_context=crm_context,
        solo_context=solo_context,
        ip_geolocation_context=ip_geolocation_context,
    )
    missing_items = _missing_data_items(
        crm_context=crm_context,
        solo_context=solo_context,
        ip_geolocation_context=ip_geolocation_context,
        public_ip_count=public_ip_count_raw,
    )
    findings_bullets = [
        f"{item.get('title')}: {item.get('detail')}" for item in findings[:8]
    ] or ["No material findings were generated from the connected sources."]
    executive_bullets = _executive_summary_bullets(
        company_name=company_name,
        license_ids=license_ids,
        file_size_gib=file_size_gib,
        files_processed=files_processed,
        links_processed=links_processed,
        findings=findings,
        crm_context=crm_context,
        solo_context=solo_context,
    )

    sections = [
        {
            "heading": "Executive Summary",
            "body": [
                f"Evaluation: {evaluation}.",
                "Investigative aid only; final EULA conclusions require human review.",
            ],
            "bullets": executive_bullets,
        },
        {
            "heading": "AWS Usage Evidence",
            "bullets": [
                f"Files processed: {files_processed}",
                f"Links processed: {links_processed}",
                f"File size processed: about {file_size_gib:,.2f} GiB",
                f"Run rows: {run_count}",
                f"Usage date range: {first_start} through {last_end}",
                f"Identity spread: {machine_count} machine name(s), {mac_count} MAC address(es), {public_ip_count} public IP address(es)",
                f"Machine names: {_sample_list(summary.get('machine_names') or [])}",
                f"MAC addresses: {_sample_list(summary.get('mac_addresses') or [])}",
                f"Public IP addresses: {_sample_list(summary.get('public_ips') or [])}",
            ],
        },
        threshold_section,
        solo_section,
        ip_section,
        crm_section,
        consistency_section,
        {
            "heading": "Findings",
            "bullets": findings_bullets,
        },
        {
            "heading": "Human Review Still Needed",
            "bullets": _human_review_steps(ip_geolocation_context),
        },
    ]
    if missing_items:
        sections.insert(
            -1,
            {
                "heading": "Missing Data Needed For A Defensible EULA Opinion",
                "bullets": missing_items,
            },
        )
    return {
        "title": f"License Violation Review: {company_name}",
        "subtitle": f"Requested subject: {requested_subject}",
        "subject": company_name,
        "evaluation": evaluation,
        "sections": sections,
    }


def _human_review_steps(ip_geolocation_context: dict[str, Any]) -> list[str]:
    steps = [
        "Confirm that the CRM organization definition is the complete contractual scope.",
        "Treat the GiB per entitlement-unit calculation as a review trigger, not as proof of a violation.",
        "Use GeoLite2 locations as approximate city/region signals, not exact physical addresses.",
    ]
    requested_ips = set(ip_geolocation_context.get("public_ips") or []) | set(ip_geolocation_context.get("activation_ips") or [])
    if ip_geolocation_context.get("records"):
        steps.append("Confirm whether any automated geography mismatches have a legitimate business explanation.")
    elif requested_ips:
        steps.append("Backfill missing IP geolocation before relying on geography conclusions.")
    else:
        steps.append("No public IP geography was available; use CRM scope and identity-spread evidence for review.")
    return steps


def _eula_threshold_report_section(
    summary: dict[str, Any],
    crm_context: dict[str, Any],
    solo_context: dict[str, Any],
) -> dict[str, Any]:
    file_size_gib = float(summary.get("file_size_gib") or 0)
    personnel_signal = _licensed_personnel_signal(crm_context=crm_context, solo_context=solo_context)
    if not personnel_signal.get("value"):
        return {
            "heading": "EULA Usage Threshold",
            "body": [
                "The 100 GiB review threshold cannot be calculated until an entitlement denominator is available."
            ],
            "bullets": [
                f"Usage volume available from AWS ProcessInfo: about {file_size_gib:,.2f} GiB",
                "Entitlement denominator: not found in connected CRM, QLI, or SOLO context",
            ],
        }
    personnel = float(personnel_signal["value"])
    gib_per_person = file_size_gib / personnel if personnel else 0
    threshold_status = "below"
    if gib_per_person >= 100:
        threshold_status = "at or above"
    return {
        "heading": "EULA Usage Threshold",
        "body": [
            f"The connected data calculates usage at about {gib_per_person:,.2f} GiB per entitlement unit, {threshold_status} the 100 GiB review threshold."
        ],
        "bullets": [
            f"Usage volume: about {file_size_gib:,.2f} GiB",
            f"Entitlement denominator: {personnel_signal['display_value']} from {personnel_signal['source']}",
            "Threshold note: this is an investigative trigger, not by itself proof of a EULA violation.",
        ],
    }


def _executive_summary_bullets(
    *,
    company_name: str,
    license_ids: str,
    file_size_gib: float,
    files_processed: str,
    links_processed: str,
    findings: list[dict[str, Any]],
    crm_context: dict[str, Any],
    solo_context: dict[str, Any],
) -> list[str]:
    bullets = [
        f"Subject: {company_name}",
        f"Usage observed: {files_processed} files, {links_processed} links, about {file_size_gib:,.2f} GiB",
    ]
    if license_ids != "unknown":
        bullets.append(f"License IDs observed in usage data: {license_ids}")
    personnel_signal = _licensed_personnel_signal(crm_context=crm_context, solo_context=solo_context)
    if personnel_signal.get("value"):
        denominator = float(personnel_signal["value"])
        gib_per_unit = file_size_gib / denominator if denominator else 0
        bullets.append(
            f"Entitlement denominator: {personnel_signal['display_value']} from {personnel_signal['source']}; about {gib_per_unit:,.2f} GiB per entitlement unit"
        )
    top_findings = [
        str(item.get("title") or item.get("code"))
        for item in findings
        if item.get("code") not in {"missing_solo_activation_timeline", "missing_crm_entitlement", "missing_ip_geolocation"}
    ]
    if top_findings:
        bullets.append("Main review indicators: " + "; ".join(top_findings[:3]))
    return bullets


def _crm_report_section(crm_context: dict[str, Any]) -> dict[str, Any]:
    if not crm_context.get("configured"):
        return {
            "heading": "CRM Relationship, Ownership, Purchase, And Communications",
            "status": "CRM Aurora relationship and entitlement data: not configured in this deployed report.",
            "body": [
                "The report cannot yet summarize relationship history, ownership, purchase records, licensed personnel, notes, communications, or sentiment from CRM."
            ],
            "bullets": [
                "Needed for relationship details: account rows and linked contacts/sites.",
                "Needed for purchase/entitlement: active LinkTek license rows, quote/order/SRF records, licensed personnel, and organization definition.",
                "Needed for communications sentiment: CRM notes, emails, calls, and Sales Routing Form narrative fields.",
            ],
        }
    if crm_context.get("error"):
        return {
            "heading": "CRM Relationship, Ownership, Purchase, And Communications",
            "status": f"CRM Aurora relationship and entitlement data: lookup failed ({crm_context['error']}).",
            "bullets": [f"Lookup error: {crm_context['error']}"],
        }
    account_rows = ((crm_context.get("account_lookup") or {}).get("rows") or [])
    license_rows = ((crm_context.get("license_lookup") or {}).get("rows") or [])
    linked_payload = crm_context.get("linked_records") or {}
    linked_licenses = linked_payload.get("licenses") or []
    linked_total = sum(
        len(records)
        for license_item in linked_licenses
        for records in (license_item.get("linked_records") or {}).values()
    )
    bullets = [
        f"CRM account matches: {len(account_rows)}",
        f"LinkTek license rows returned: {len(license_rows)} ({_crm_license_lookup_scope_label((crm_context.get('license_lookup') or {}).get('lookup_scope'))})",
        f"Linked CRM records sampled: {linked_total}",
    ]
    if account_rows:
        bullets.extend(_crm_account_bullets(account_rows))
    if license_rows:
        bullets.extend(_crm_license_bullets(license_rows))
    bullets.extend(_crm_linked_record_bullets(linked_licenses))
    if not account_rows and not license_rows:
        bullets.append("No CRM account or LinkTek license rows were returned for this subject.")
    return {
        "heading": "CRM Relationship, Ownership, Purchase, And Communications",
        "status": "CRM Aurora relationship and entitlement data: connected.",
        "body": [
            "CRM lookups are available through Aurora. Email bodies are not present in this Aurora sync; available communication context comes from notes, calls, meetings, tasks, deals, SRFs, and license-verification records."
        ],
        "bullets": bullets,
    }


def _crm_license_lookup_scope_label(scope: Any) -> str:
    labels = {
        "active": "active company/name lookup",
        "active_by_usage_license": "active lookup by AWS usage license ID",
        "historical": "historical company/name lookup",
        "historical_by_usage_license": "historical lookup by AWS usage license ID",
    }
    return labels.get(str(scope or ""), "lookup scope unknown")


def _crm_account_bullets(account_rows: list[dict[str, Any]]) -> list[str]:
    bullets: list[str] = []
    for row in account_rows[:3]:
        record_type = row.get("record_type") or "record"
        company = row.get("company_name") or row.get("account_name") or row.get("name") or "unknown"
        account = row.get("account_name")
        owner = row.get("owner_name")
        entity = row.get("entity")
        detail = f"{record_type}: {company}"
        if account and account != company:
            detail += f" / site {account}"
        if owner:
            detail += f"; owner {owner}"
        if entity:
            detail += f"; entity {entity}"
        bullets.append(detail)
    return bullets


def _crm_license_bullets(license_rows: list[dict[str, Any]]) -> list[str]:
    bullets: list[str] = []
    for row in license_rows[:3]:
        label = row.get("name") or row.get("license_code") or row.get("id") or "license"
        expiry = row.get("maintenance_expiry_date") or "unknown expiry"
        product = row.get("product") or "unknown product"
        company = row.get("company_name") or row.get("company") or "unknown company"
        entitlement = _entitlement_signal_from_customer_license(row)
        count_basis = row.get("which_count_to_use")
        estimated_personnel = row.get("estimated_personnel_count")
        scope = row.get("organization_description") or row.get("which_count_to_use")
        active_label = "Active license" if row.get("active_license") is True else "Historical license"
        detail = f"{active_label} {label}: {company}; product {product}; expires {expiry}"
        identity_bits = []
        if row.get("license_code"):
            identity_bits.append(f"license code {row.get('license_code')}")
        if row.get("serial_number"):
            identity_bits.append(f"serial {row.get('serial_number')}")
        if row.get("gm_serial_number"):
            identity_bits.append(f"GM serial {row.get('gm_serial_number')}")
        if row.get("qlm_license_key"):
            identity_bits.append("QLM key present")
        if row.get("solo_password_present"):
            identity_bits.append("SOLO password present")
        if identity_bits:
            detail += "; " + "; ".join(identity_bits)
        if entitlement.get("value"):
            detail += f"; entitlement denominator {entitlement['display_value']} ({count_basis or 'basis unspecified'})"
        if estimated_personnel not in (None, ""):
            detail += f"; estimated personnel {estimated_personnel}"
        if row.get("employee_or_computer_count") not in (None, ""):
            detail += f"; employee/computer count {row.get('employee_or_computer_count')}"
        if row.get("subset_license_count") not in (None, ""):
            detail += f"; subset license count {row.get('subset_license_count')}"
        if row.get("entire_legal_entity_personnel_count_3") not in (None, ""):
            detail += f"; entire legal entity personnel {row.get('entire_legal_entity_personnel_count_3')}"
        if scope:
            detail += f"; scope {scope}"
        if row.get("possible_violation"):
            detail += f"; CRM possible_violation={row.get('possible_violation')}"
        bullets.append(detail)
    return bullets


def _crm_linked_record_bullets(linked_licenses: list[dict[str, Any]]) -> list[str]:
    bullets: list[str] = []
    for item in linked_licenses[:2]:
        linked = item.get("linked_records") or {}
        counts = {name: len(rows) for name, rows in linked.items()}
        if counts:
            bullets.append(
                "Linked records for sampled license: "
                + ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))
            )
        for lv in (linked.get("license_verifications") or [])[:2]:
            detail = f"License Verification {lv.get('name') or lv.get('id')}: stage {lv.get('stage') or 'unknown'}"
            if lv.get("organization_definition"):
                detail += f"; org definition {lv.get('organization_definition')}"
            if lv.get("personnel_count") is not None:
                detail += f"; licensed personnel {lv.get('personnel_count')}"
            if lv.get("estimated_personnel_count") is not None:
                detail += f"; estimated personnel {lv.get('estimated_personnel_count')}"
            if lv.get("current_status"):
                detail += f"; status {_truncate(str(lv.get('current_status')), 160)}"
            bullets.append(detail)
        for srf in (linked.get("sales_routing_forms") or [])[:2]:
            detail = f"Sales Routing Form {srf.get('name') or srf.get('id')}: owner {srf.get('owner_name') or 'unknown'}"
            if srf.get("license_violation") is not None:
                detail += f"; license_violation={srf.get('license_violation')}"
            if srf.get("unresolved_license_violation") is not None:
                detail += f"; unresolved_license_violation={srf.get('unresolved_license_violation')}"
            bullets.append(detail)
        qli_quantity = _quantity_from_rows(linked.get("quote_line_item_sets") or [])
        if qli_quantity is not None:
            bullets.append(f"Quote line items: entitlement quantity signal {_format_int(qli_quantity)}")
    return bullets


def _solo_report_section(solo_context: dict[str, Any]) -> dict[str, Any]:
    metrics = solo_context.get("metrics") or {}
    if not solo_context.get("configured") or not metrics:
        status = "SOLO activation/export summary: no matching curated summary was found for this subject."
        if solo_context.get("error"):
            status = f"SOLO activation/export summary: lookup failed ({solo_context['error']})."
        return {
            "heading": "SOLO Activation Evidence",
            "status": status,
            "body": [
                "The report cannot yet compare first activation date, activation IP locations, license status, deactivation dates, or first activation-to-first usage delay in the deployed runtime."
            ],
            "bullets": [
                "Needed: curated SOLO activation and Export Licenses summaries in S3, keyed by License ID and Customer ID.",
                "Needed: activation IP geolocation cache for city, state, and country comparison.",
            ],
        }
    bullets = [
        f"Activation rows: {_format_int(metrics.get('activations'))}",
        f"Successful activations: {_format_int(metrics.get('successful_activations'))}",
        f"Rejected activations: {_format_int(metrics.get('rejected_activations'))}",
        f"Unique activation IPs: {_format_int(metrics.get('unique_ips'))}",
        f"Unique installation IDs: {_format_int(metrics.get('unique_installations'))}",
        f"Unique computer IDs: {_format_int(metrics.get('unique_computers'))}",
        f"Deactivations: {_format_int(metrics.get('deactivations'))}",
        f"Unique SOLO licenses: {_format_int(metrics.get('unique_licenses'))}",
    ]
    if metrics.get("q_ordered_total"):
        bullets.append(
            f"SOLO Export Licenses QOrdered total: {_format_int(metrics.get('q_ordered_total'))} across {_format_int(metrics.get('q_ordered_license_count'))} license row(s)"
        )
    return {
        "heading": "SOLO Activation Evidence",
        "status": "SOLO activation/export summary: connected from curated summary data.",
        "bullets": bullets,
    }


def _ip_geolocation_report_section(ip_context: dict[str, Any], public_ips: list[Any]) -> dict[str, Any]:
    records = ip_context.get("records") or {}
    public_ip_values = [str(value) for value in (ip_context.get("public_ips") or public_ips) if str(value).strip()]
    activation_ip_values = [str(value) for value in ip_context.get("activation_ips") or [] if str(value).strip()]
    requested_ips = sorted(set(public_ip_values + activation_ip_values))
    expected_count = len(requested_ips)
    if expected_count == 0:
        return {
            "heading": "IP Geolocation Evidence",
            "status": "No public IP addresses were present in the AWS usage summary for this subject.",
            "body": [
                "There are no AWS ProcessInfo public IPs or SOLO activation IPs to geolocate for this report."
            ],
            "bullets": [],
        }
    if not records:
        status = "IP geolocation cache: not connected yet."
        if ip_context.get("error"):
            status = f"IP geolocation cache: lookup failed ({ip_context['error']})."
        return {
            "heading": "IP Geolocation Evidence",
            "status": status,
            "body": [
                "Public IP spread cannot yet be compared to allowed organization geography."
            ],
            "bullets": [
                "Needed: GeoLite2 backfill cache keyed by public IP address.",
                "GeoLite2 is approximate; reports should use city/region/country plus accuracy radius, not street-level conclusions.",
            ],
        }
    locations = []
    for ip_address, record in sorted(records.items()):
        if not isinstance(record, dict):
            continue
        city = record.get("city") or "unknown city"
        region = record.get("region") or ""
        country = record.get("country") or "unknown country"
        radius = record.get("accuracy_radius_km")
        radius_text = f", radius {radius} km" if radius not in ("", None) else ""
        region_text = f", {region}" if region else ""
        sources = []
        if ip_address in set(public_ip_values):
            sources.append("AWS usage")
        if ip_address in set(activation_ip_values):
            sources.append("SOLO activation")
        source_text = f" ({', '.join(sources)})" if sources else ""
        locations.append(f"{ip_address}{source_text}: {city}{region_text}, {country}{radius_text}")
    return {
        "heading": "IP Geolocation Evidence",
        "status": f"IP geolocation cache: connected ({len(records)} of {expected_count} public IPs found in cache).",
        "body": [
            "Locations are GeoLite2 city-level approximations. Treat them as investigative signals, not exact office addresses."
        ],
        "bullets": locations[:10] or ["No cache records were available for the listed public IPs."],
    }


def _automated_consistency_report_section(
    *,
    summary: dict[str, Any],
    crm_context: dict[str, Any],
    solo_context: dict[str, Any],
    ip_geolocation_context: dict[str, Any],
) -> dict[str, Any]:
    check = _automated_consistency_check(
        summary=summary,
        crm_context=crm_context,
        solo_context=solo_context,
        ip_geolocation_context=ip_geolocation_context,
    )
    return {
        "heading": "Automated Consistency Checks",
        "body": [
            "These checks are performed by the agent from connected CRM, SOLO, AWS usage, and geolocation data. They are evidence triage, not final legal conclusions."
        ],
        "bullets": check["bullets"],
    }


def _automated_consistency_findings(
    *,
    summary: dict[str, Any],
    crm_context: dict[str, Any],
    solo_context: dict[str, Any],
    ip_geolocation_context: dict[str, Any],
) -> list[dict[str, Any]]:
    check = _automated_consistency_check(
        summary=summary,
        crm_context=crm_context,
        solo_context=solo_context,
        ip_geolocation_context=ip_geolocation_context,
    )
    findings: list[dict[str, Any]] = []
    geography = check["geography"]
    if geography["outside_locations"]:
        findings.append(
            {
                "code": "geography_outside_crm_scope",
                "title": "Geography appears outside CRM organization scope",
                "severity": "medium",
                "detail": (
                    "The CRM organization definition contains geography hints, and one or more geolocated "
                    "AWS/SOLO IP locations are outside those hints."
                ),
                "evidence": {
                    "scope_texts": geography["scope_texts"],
                    "allowed_countries": sorted(geography["allowed_countries"]),
                    "allowed_regions": sorted(geography["allowed_regions"]),
                    "outside_locations": geography["outside_locations"],
                },
            }
        )
    prior = check["prior_review"]
    if prior["flagged_records"]:
        findings.append(
            {
                "code": "prior_crm_violation_signal",
                "title": "Prior CRM records contain license-violation signals",
                "severity": "high" if prior["unresolved_count"] else "medium",
                "detail": (
                    f"CRM linked records include {prior['flagged_count']} license-violation flag(s), "
                    f"including {prior['unresolved_count']} unresolved flag(s)."
                ),
                "evidence": {"flagged_records": prior["flagged_records"]},
            }
        )
    count_check = check["counts"]
    if count_check["mismatches"]:
        findings.append(
            {
                "code": "entitlement_count_inconsistency",
                "title": "Entitlement count signals are inconsistent",
                "severity": "medium",
                "detail": "CRM Customer License, quote line item, or license-verification count signals do not agree.",
                "evidence": {"signals": count_check["signals"], "mismatches": count_check["mismatches"]},
            }
        )
    return findings


def _automated_consistency_check(
    *,
    summary: dict[str, Any],
    crm_context: dict[str, Any],
    solo_context: dict[str, Any],
    ip_geolocation_context: dict[str, Any],
) -> dict[str, Any]:
    geography = _geography_consistency_check(crm_context, ip_geolocation_context)
    prior_review = _prior_review_consistency_check(crm_context)
    counts = _count_consistency_check(summary, crm_context, solo_context)
    bullets: list[str] = []
    bullets.extend(_geography_check_bullets(geography))
    bullets.extend(_prior_review_check_bullets(prior_review))
    bullets.extend(_count_check_bullets(counts))
    return {
        "geography": geography,
        "prior_review": prior_review,
        "counts": counts,
        "bullets": bullets or ["No automated consistency checks could be completed from the connected data."],
    }


def _geography_consistency_check(crm_context: dict[str, Any], ip_context: dict[str, Any]) -> dict[str, Any]:
    scope_texts = _organization_scope_texts(crm_context)
    scope = _parse_scope_geography(scope_texts)
    records = ip_context.get("records") or {}
    outside_locations: list[str] = []
    inside_locations: list[str] = []
    inconclusive_locations: list[str] = []
    for ip_address, record in sorted(records.items()):
        if not isinstance(record, dict):
            continue
        country = str(record.get("country") or "").strip()
        region = str(record.get("region") or "").strip()
        city = str(record.get("city") or "").strip()
        label = _geo_record_label(ip_address, record)
        if not scope["allowed_countries"] and not scope["allowed_regions"] and not scope["allowed_cities"]:
            inconclusive_locations.append(label)
            continue
        if _geo_record_within_scope(country=country, region=region, city=city, scope=scope):
            inside_locations.append(label)
        else:
            outside_locations.append(label)
    return {
        **scope,
        "scope_texts": scope_texts,
        "inside_locations": inside_locations,
        "outside_locations": outside_locations,
        "inconclusive_locations": inconclusive_locations,
        "checked_location_count": len(inside_locations) + len(outside_locations) + len(inconclusive_locations),
    }


def _prior_review_consistency_check(crm_context: dict[str, Any]) -> dict[str, Any]:
    flagged_records: list[str] = []
    unresolved_count = 0
    srf_count = 0
    lv_count = 0
    for item in ((crm_context.get("linked_records") or {}).get("licenses") or []):
        linked = item.get("linked_records") or {}
        for srf in linked.get("sales_routing_forms") or []:
            srf_count += 1
            label = f"Sales Routing Form {srf.get('name') or srf.get('id') or 'unknown'}"
            if _truthy(srf.get("license_violation")) or _truthy(srf.get("unresolved_license_violation")):
                if _truthy(srf.get("unresolved_license_violation")):
                    unresolved_count += 1
                flagged_records.append(
                    f"{label}: license_violation={srf.get('license_violation')}, unresolved_license_violation={srf.get('unresolved_license_violation')}"
                )
        for lv in linked.get("license_verifications") or []:
            lv_count += 1
            status = " ".join(str(value or "") for value in (lv.get("stage"), lv.get("current_status"))).lower()
            if any(word in status for word in ("violation", "unresolved", "investigat")):
                flagged_records.append(
                    f"License Verification {lv.get('name') or lv.get('id') or 'unknown'}: stage={lv.get('stage') or 'unknown'}; status={_truncate(str(lv.get('current_status') or ''), 120)}"
                )
    return {
        "srf_count": srf_count,
        "license_verification_count": lv_count,
        "flagged_count": len(flagged_records),
        "unresolved_count": unresolved_count,
        "flagged_records": flagged_records,
    }


def _count_consistency_check(
    summary: dict[str, Any],
    crm_context: dict[str, Any],
    solo_context: dict[str, Any],
) -> dict[str, Any]:
    signals: list[dict[str, Any]] = []
    personnel_signal = _licensed_personnel_signal(crm_context=crm_context, solo_context=solo_context)
    if personnel_signal.get("value"):
        signals.append({"source": personnel_signal.get("source"), "value": float(personnel_signal["value"]), "role": "entitlement_denominator"})
    for item in ((crm_context.get("linked_records") or {}).get("licenses") or []):
        linked = item.get("linked_records") or {}
        qli_quantity = _quantity_from_rows(linked.get("quote_line_item_sets") or [])
        if qli_quantity is not None:
            signals.append({"source": "CRM quote line item quantity", "value": float(qli_quantity), "role": "quote_quantity"})
        for lv in linked.get("license_verifications") or []:
            value = _positive_number(lv.get("personnel_count"))
            if value is not None:
                signals.append({"source": "CRM License Verification personnel_count", "value": float(value), "role": "verification_personnel"})
    metrics = solo_context.get("metrics") or {}
    solo_quantity = _positive_number(metrics.get("solo_entitlement_count") or metrics.get("q_ordered_total"))
    if solo_quantity is not None:
        signals.append({"source": metrics.get("solo_entitlement_source") or "SOLO export quantity", "value": float(solo_quantity), "role": "solo_quantity"})
    mismatches: list[str] = []
    comparable = [item for item in signals if item["role"] in {"entitlement_denominator", "quote_quantity", "verification_personnel"}]
    if len(comparable) > 1:
        values = [item["value"] for item in comparable]
        if min(values) > 0 and max(values) / min(values) > 1.1:
            mismatches.append(
                "Comparable CRM entitlement/count signals differ: "
                + "; ".join(f"{item['source']}={_format_int(item['value'])}" for item in comparable)
            )
    if summary.get("license_ids") and metrics.get("license_ids"):
        usage_license_ids = {str(value) for value in summary.get("license_ids") or []}
        solo_license_ids = {str(value) for value in metrics.get("license_ids") or []}
        if not usage_license_ids.intersection(solo_license_ids):
            mismatches.append("AWS usage license IDs do not overlap the matched SOLO summary license IDs.")
    return {"signals": signals, "mismatches": mismatches}


def _geography_check_bullets(geography: dict[str, Any]) -> list[str]:
    if not geography["scope_texts"]:
        return ["Geography scope check: no CRM organization definition text was available to parse."]
    scope_bits = []
    if geography["allowed_countries"]:
        scope_bits.append("countries " + ", ".join(sorted(geography["allowed_countries"])))
    if geography["allowed_regions"]:
        scope_bits.append("regions/states " + ", ".join(sorted(geography["allowed_regions"])))
    if geography["allowed_cities"]:
        scope_bits.append("cities " + ", ".join(sorted(geography["allowed_cities"])))
    if not scope_bits:
        return ["Geography scope check: CRM organization definition was present, but no usable city/state/country hints were parsed."]
    bullets = [f"Geography scope check: parsed allowed {'; '.join(scope_bits)} from CRM organization definition."]
    if geography["outside_locations"]:
        bullets.append(
            f"Geography scope check: {len(geography['outside_locations'])} geolocated IP location(s) appear outside the parsed CRM scope. Sample: {_sample_list(geography['outside_locations'], limit=4)}"
        )
    elif geography["inside_locations"]:
        bullets.append(f"Geography scope check: all {len(geography['inside_locations'])} geolocated IP location(s) matched the parsed CRM scope.")
    elif geography["inconclusive_locations"]:
        bullets.append("Geography scope check: IP locations were available, but the parsed scope was not specific enough for a confident comparison.")
    return bullets


def _prior_review_check_bullets(prior_review: dict[str, Any]) -> list[str]:
    bullets = [
        f"Prior CRM review check: sampled {prior_review['srf_count']} Sales Routing Form record(s) and {prior_review['license_verification_count']} License Verification record(s)."
    ]
    if prior_review["flagged_records"]:
        bullets.append(
            f"Prior CRM review check: found {prior_review['flagged_count']} prior license-violation signal(s), including {prior_review['unresolved_count']} unresolved flag(s). Sample: {_sample_list(prior_review['flagged_records'], limit=3)}"
        )
    else:
        bullets.append("Prior CRM review check: no sampled SRF or License Verification record contained a prior violation flag.")
    return bullets


def _count_check_bullets(counts: dict[str, Any]) -> list[str]:
    if not counts["signals"]:
        return ["Count consistency check: no entitlement or license-count signals were available."]
    bullets = [
        "Count consistency check: "
        + "; ".join(f"{item['source']}={_format_int(item['value'])}" for item in counts["signals"][:5])
    ]
    if counts["mismatches"]:
        bullets.append("Count consistency check: " + " ".join(counts["mismatches"]))
    else:
        bullets.append("Count consistency check: comparable CRM entitlement/count signals are consistent or not directly comparable.")
    return bullets


def _organization_scope_texts(crm_context: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for row in ((crm_context.get("license_lookup") or {}).get("rows") or []):
        for key in ("organization_description", "scope", "site", "company_name", "company"):
            value = row.get(key)
            if value:
                texts.append(str(value))
    for item in ((crm_context.get("linked_records") or {}).get("licenses") or []):
        linked = item.get("linked_records") or {}
        for lv in linked.get("license_verifications") or []:
            for key in ("organization_definition", "current_status"):
                value = lv.get(key)
                if value:
                    texts.append(str(value))
    return _dedupe_preserve_order([text.strip() for text in texts if text.strip()])


def _parse_scope_geography(scope_texts: list[str]) -> dict[str, set[str]]:
    text = " ".join(scope_texts).lower()
    allowed_countries = {
        canonical
        for canonical, aliases in COUNTRY_HINTS.items()
        if any(re.search(rf"\b{re.escape(alias)}\b", text) for alias in aliases)
    }
    allowed_regions = {
        canonical
        for canonical, aliases in REGION_HINTS.items()
        if any(re.search(rf"\b{re.escape(alias)}\b", text) for alias in aliases)
    }
    allowed_cities = {
        canonical
        for canonical, payload in CITY_HINTS.items()
        if re.search(rf"\b{re.escape(canonical)}\b", text)
    }
    for city in allowed_cities:
        payload = CITY_HINTS.get(city) or {}
        if payload.get("country"):
            allowed_countries.add(str(payload["country"]))
        if payload.get("region"):
            allowed_regions.add(str(payload["region"]))
    return {
        "allowed_countries": allowed_countries,
        "allowed_regions": allowed_regions,
        "allowed_cities": allowed_cities,
    }


def _geo_record_within_scope(*, country: str, region: str, city: str, scope: dict[str, set[str]]) -> bool:
    country_key = country.lower().strip()
    region_key = region.lower().strip()
    city_key = city.lower().strip()
    if scope["allowed_regions"]:
        return region_key in scope["allowed_regions"]
    if scope["allowed_cities"]:
        return city_key in scope["allowed_cities"]
    if scope["allowed_countries"]:
        return country_key in scope["allowed_countries"]
    return False


def _geo_record_label(ip_address: str, record: dict[str, Any]) -> str:
    city = record.get("city") or "unknown city"
    region = record.get("region") or ""
    country = record.get("country") or "unknown country"
    region_text = f", {region}" if region else ""
    return f"{ip_address}: {city}{region_text}, {country}"


COUNTRY_HINTS = {
    "australia": ("australia",),
    "belgium": ("belgium",),
    "canada": ("canada",),
    "china": ("china",),
    "denmark": ("denmark",),
    "germany": ("germany",),
    "hong kong": ("hong kong",),
    "india": ("india",),
    "malaysia": ("malaysia",),
    "netherlands": ("netherlands", "holland"),
    "singapore": ("singapore",),
    "south africa": ("south africa",),
    "united kingdom": ("united kingdom", "uk", "england", "scotland", "wales"),
    "united states": ("united states", "usa", "u.s.", "us"),
}


REGION_HINTS = {
    "california": ("california", "ca"),
    "florida": ("florida", "fl"),
    "limburg": ("limburg",),
    "new south wales": ("new south wales", "nsw"),
    "new york": ("new york", "ny"),
    "ontario": ("ontario",),
    "texas": ("texas", "tx"),
    "utah": ("utah", "ut"),
    "western cape": ("western cape",),
}


CITY_HINTS = {
    "roermond": {"region": "limburg", "country": "netherlands"},
    "cape town": {"region": "western cape", "country": "south africa"},
    "sydney": {"region": "new south wales", "country": "australia"},
    "new york": {"region": "new york", "country": "united states"},
}


def _missing_data_items(
    *,
    crm_context: dict[str, Any],
    solo_context: dict[str, Any],
    ip_geolocation_context: dict[str, Any],
    public_ip_count: int,
) -> list[str]:
    items: list[str] = []
    if not solo_context.get("configured"):
        items.append("No matching SOLO activation/export summary was found for this subject.")
    if not crm_context.get("configured"):
        items.append("CRM relationship, purchase, entitlement, organization definition, notes, and communications are missing from the deployed report.")
    activation_ip_count = len(ip_geolocation_context.get("activation_ips") or [])
    if (public_ip_count > 0 or activation_ip_count > 0) and not (ip_geolocation_context.get("records") or {}):
        items.append("IP geolocation is missing, so public IP spread cannot yet be compared to allowed organization geography.")
    if not _licensed_personnel_signal(crm_context=crm_context, solo_context=solo_context).get("value"):
        items.append("Entitlement count is missing, so the 100 GiB per entitlement-unit review threshold cannot yet be calculated.")
    return items


def _has_crm_entitlement_context(crm_context: dict[str, Any]) -> bool:
    if not crm_context.get("configured") or crm_context.get("error"):
        return False
    license_rows = ((crm_context.get("license_lookup") or {}).get("rows") or [])
    if any(row.get("organization_description") or row.get("which_count_to_use") for row in license_rows):
        return True
    linked_licenses = ((crm_context.get("linked_records") or {}).get("licenses") or [])
    for item in linked_licenses:
        linked = item.get("linked_records") or {}
        for lv in linked.get("license_verifications") or []:
            if lv.get("organization_definition") or lv.get("personnel_count") is not None:
                return True
    return False


def _licensed_personnel_signal(
    *,
    crm_context: dict[str, Any],
    solo_context: dict[str, Any],
) -> dict[str, Any]:
    license_rows = ((crm_context.get("license_lookup") or {}).get("rows") or [])
    for row in license_rows:
        signal = _entitlement_signal_from_customer_license(row)
        if signal.get("value"):
            return signal

    for item in ((crm_context.get("linked_records") or {}).get("licenses") or []):
        linked = item.get("linked_records") or {}
        for row in linked.get("license_verifications") or []:
            value = _positive_number(row.get("personnel_count"))
            if value is not None:
                return _personnel_signal(value, "CRM License Verification personnel_count")

    for item in ((crm_context.get("linked_records") or {}).get("licenses") or []):
        linked = item.get("linked_records") or {}
        value = _quantity_from_rows(linked.get("quote_line_item_sets") or [])
        if value is not None:
            return _personnel_signal(value, "CRM quote line item quantity")

    metrics = solo_context.get("metrics") or {}
    if metrics.get("solo_entitlement_count"):
        return _personnel_signal(float(metrics["solo_entitlement_count"]), metrics.get("solo_entitlement_source") or "SOLO Export Licenses QOrdered total")
    for key in ("licensed_personnel_count", "personnel_licensed", "q_ordered", "QOrdered", "q_ordered_total"):
        value = _positive_number(metrics.get(key))
        if value is not None:
            return _personnel_signal(value, f"SOLO export {key}")

    return {"value": None, "display_value": "unknown", "source": "not found"}


def _entitlement_signal_from_customer_license(row: dict[str, Any]) -> dict[str, Any]:
    basis = str(row.get("which_count_to_use") or "").strip()
    basis_lower = basis.lower()
    if "employee" in basis_lower or "computer" in basis_lower or "personnel" in basis_lower or "user" in basis_lower:
        value = _positive_number(row.get("employee_or_computer_count"))
        if value is not None:
            return _personnel_signal(value, f"CRM Customer License employee_or_computer_count ({basis})")
    if "subset" in basis_lower:
        value = _positive_number(row.get("subset_license_count"))
        if value is not None:
            return _personnel_signal(value, f"CRM Customer License subset_license_count ({basis})")
    if "entire" in basis_lower or "legal entity" in basis_lower:
        value = _positive_number(row.get("entire_legal_entity_personnel_count_3"))
        if value is not None:
            return _personnel_signal(value, f"CRM Customer License entire_legal_entity_personnel_count_3 ({basis})")
    if "site" in basis_lower:
        for key in ("site_count", "single_quantity"):
            value = _positive_number(row.get(key))
            if value is not None:
                return _personnel_signal(value, f"CRM Customer License {key} ({basis})")
    if "single" in basis_lower:
        value = _positive_number(row.get("single_quantity"))
        if value is not None:
            return _personnel_signal(value, f"CRM Customer License single_quantity ({basis})")
    if "link" in basis_lower:
        value = _positive_number(row.get("link_limit") or row.get("links"))
        if value is not None:
            return _personnel_signal(value, f"CRM Customer License link_limit ({basis})")

    fallback_order = (
        ("employee_or_computer_count", "CRM Customer License employee_or_computer_count"),
        ("subset_license_count", "CRM Customer License subset_license_count"),
        ("entire_legal_entity_personnel_count_3", "CRM Customer License entire_legal_entity_personnel_count_3"),
        ("site_count", "CRM Customer License site_count"),
        ("single_quantity", "CRM Customer License single_quantity"),
        ("total_seat_count", "CRM Customer License total_seat_count"),
    )
    for key, source in fallback_order:
        value = _positive_number(row.get(key))
        if value is not None:
            suffix = f" ({basis})" if basis else ""
            return _personnel_signal(value, source + suffix)
    return {"value": None, "display_value": "unknown", "source": "not found"}


def _personnel_signal(value: float, source: str) -> dict[str, Any]:
    display_value = _format_int(value) if float(value).is_integer() else f"{value:,.2f}"
    return {"value": value, "display_value": display_value, "source": source}


def _positive_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    return number


def _quantity_from_rows(rows: list[dict[str, Any]]) -> float | None:
    total = 0.0
    found = False
    quantity_keys = (
        "personnel_count",
        "licensed_personnel_count",
        "license_count",
        "quantity",
        "Quantity",
        "qty",
        "Qty",
        "QTY",
        "number_of_users",
        "user_count",
        "seat_count",
        "total_seat_count",
        "employee_or_computer_count",
    )
    for row in rows:
        for key in quantity_keys:
            value = _positive_number(row.get(key))
            if value is None:
                continue
            total += value
            found = True
            break
    if not found:
        return None
    return total


def _sample_list(values: list[Any], *, limit: int = 8) -> str:
    cleaned = [str(value) for value in values if str(value).strip()]
    if not cleaned:
        return "none found"
    suffix = "" if len(cleaned) <= limit else f" plus {len(cleaned) - limit} more"
    return ", ".join(cleaned[:limit]) + suffix


def _sample_record_labels(rows: list[dict[str, Any]], *, limit: int = 3) -> str:
    labels: list[str] = []
    for row in rows[:limit]:
        for key in ("company_name", "Company Name", "account_name", "name", "license_code", "id"):
            value = row.get(key)
            if value:
                labels.append(str(value))
                break
        else:
            labels.append(str(row)[:120])
    suffix = "" if len(rows) <= limit else f" plus {len(rows) - limit} more"
    return "; ".join(labels) + suffix


def _first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y", "checked"}


def _truncate(value: str, limit: int) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 3].rstrip() + "..."


def _selection_index(text: str) -> int | None:
    lowered = text.lower().strip()
    number_match = re.search(r"\b([1-5])\b", lowered)
    if number_match:
        return int(number_match.group(1)) - 1
    word_indexes = {
        "first": 0,
        "second": 1,
        "third": 2,
        "fourth": 3,
        "fifth": 4,
    }
    for word, index in word_indexes.items():
        if re.search(rf"\b{word}\b", lowered):
            return index
    return None


def _format_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


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
