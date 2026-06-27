from __future__ import annotations

import importlib.util
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .correlation_analysis import normalize_company_name
from .ip_geolocation_cache import IpGeolocationCacheClient
from .settings import LicenseAgentSettings
from .solo_analysis import SoloCompanyMetric, build_company_metrics, find_all_csvs, read_csv_rows_many
from .usage_summary import UsageSummaryClient


@dataclass(frozen=True)
class DataQueryResult:
    kind: str
    message: str
    evidence: dict[str, Any]


class DataQueryService:
    """Answers constrained chat questions from local analysis files and optional Aurora CRM data."""

    def __init__(
        self,
        settings: LicenseAgentSettings,
        *,
        analysis_root: str | Path = "local_data/analysis/solo_signal_review",
        solo_activation_root: str | Path = "local_data/raw/solo_softwarekey/activation_data",
        aurora_client: "AuroraReadOnlyClient | None" = None,
        usage_client: UsageSummaryClient | None = None,
        ip_geolocation_client: IpGeolocationCacheClient | None = None,
    ) -> None:
        self.settings = settings
        self.analysis_root = Path(analysis_root)
        self.solo_activation_root = Path(solo_activation_root)
        self.aurora_client = aurora_client or AuroraReadOnlyClient(settings)
        self.usage_client = usage_client or UsageSummaryClient(settings)
        self.ip_geolocation_client = ip_geolocation_client or IpGeolocationCacheClient(settings)

    def runtime_status(self) -> dict[str, Any]:
        return {
            "latest_solo_signal_report": str(self._latest_cohort_report() or ""),
            "aurora": self.aurora_client.status(),
            "aws_usage_summary": self.usage_client.status(),
            "solo_activation_summary": self._solo_summary_status(),
            "ip_geolocation_cache": self.ip_geolocation_client.status(),
        }

    def answer(self, text: str) -> DataQueryResult:
        lower = text.lower()
        if _asks_about_usage_activity(lower):
            return self._answer_usage_activity(text)
        if _asks_about_linked_license_records(lower):
            return self._answer_linked_active_license_records(text)
        if _asks_about_active_linktek_licenses(lower):
            return self._answer_active_linktek_licenses(text)
        if _asks_about_crm(lower):
            return self._answer_crm_lookup(text)
        if _asks_about_dataset(lower):
            return self._answer_dataset_summary()
        if _asks_about_signals(lower):
            return self._answer_signal_summary()
        company_name = extract_company_name(text)
        if company_name:
            return self._answer_company_lookup(company_name)
        return DataQueryResult(
            kind="unsupported_query",
            message=(
                "I can answer the parts of license-analysis data that are connected right now: SOLO signal review, "
                "violator overlap, dataset coverage, and CRM lookup once Aurora is configured. I do not yet have "
                "the live AWS usage warehouse connected in Teams."
            ),
            evidence={"query": text},
        )

    def _answer_usage_activity(self, text: str) -> DataQueryResult:
        company_name = extract_company_name(text) or extract_usage_company_name(text)
        subject = company_name or "that company/license"
        if company_name:
            lookup = self.usage_client.find_company(company_name)
            if lookup.get("error"):
                return DataQueryResult(
                    kind="usage_activity_error",
                    message=(
                        f"I understood this as an AWS usage question for `{subject}`, but the usage summary lookup "
                        f"failed: {lookup['error']}"
                    ),
                    evidence={"query": text, "company_name": company_name, "usage_lookup": lookup},
                )
            match = lookup.get("match")
            if match:
                message = _usage_summary_message(match, requested_company=company_name)
                return DataQueryResult(
                    kind="usage_activity",
                    message=message,
                    evidence={"query": text, "company_name": company_name, "usage_summary": match},
                )
        return DataQueryResult(
            kind="usage_activity_unavailable",
            message=(
                f"I understood this as an AWS usage question for `{subject}`. I cannot verify file/link counts from "
                "Teams yet because the ProcessInfo usage summary has not been generated or connected to this "
                "deployment. The raw ProcessInfo data is now seeded in S3, and the next step is publishing the "
                "curated usage summary or Athena table that this bot can query quickly."
            ),
            evidence={
                "query": text,
                "company_name": company_name,
                "missing_dependency": "aws_usage_summary",
                "expected_source": "ProcessInfo",
            },
        )

    def _answer_dataset_summary(self) -> DataQueryResult:
        report = self._load_latest_cohort_report()
        if not report:
            return self._missing_report_result()
        message = (
            "The current SOLO analysis has "
            f"{report.get('solo_export_license_count', 0)} exported licenses, "
            f"{report.get('solo_activation_license_count', 0)} activation licenses, and "
            f"{report.get('solo_activation_company_count', 0)} activation companies across "
            f"{report.get('solo_activation_path_count', 0)} activation file(s). "
            f"It found {report.get('license_verification_overlap_count', 0)} License Verification violator overlaps "
            f"and {report.get('broad_srf_overlap_count', 0)} broader Sales Routing Form violator overlaps."
        )
        return DataQueryResult(kind="dataset_summary", message=message, evidence=report)

    def _answer_signal_summary(self) -> DataQueryResult:
        report = self._load_latest_cohort_report()
        if not report:
            return self._missing_report_result()
        general = report.get("general_population_summary") or {}
        violators = report.get("license_verification_overlap_summary") or {}
        outpoints = report.get("outpoints") or []
        message = (
            "The strongest signals in the expanded SOLO history are higher rejected-activation rates, "
            "multiple activation IPs, multiple installation IDs, and deactivation activity. "
            f"Violator-overlap companies had rejections in {_pct(violators.get('share_with_rejections'))} "
            f"of cases versus {_pct(general.get('share_with_rejections'))} generally; multiple IPs in "
            f"{_pct(violators.get('share_with_multi_ip'))} versus {_pct(general.get('share_with_multi_ip'))}; "
            f"and multiple installation IDs in {_pct(violators.get('share_with_multi_installation'))} versus "
            f"{_pct(general.get('share_with_multi_installation'))}."
        )
        if outpoints:
            message += " " + " ".join(str(item) for item in outpoints[:2])
        return DataQueryResult(
            kind="signal_summary",
            message=message,
            evidence={"general": general, "violators": violators, "outpoints": outpoints},
        )

    def _answer_company_lookup(self, company_name: str) -> DataQueryResult:
        report = self._load_latest_cohort_report()
        if not report:
            return self._missing_report_result()
        company_key = normalize_company_name(company_name)
        lv_matches = {
            item.get("company_key"): item
            for item in report.get("license_verification_overlap_companies", [])
            if isinstance(item, dict)
        }
        broad_matches = set(report.get("broad_srf_overlap_companies") or [])
        metrics = self._company_metrics(company_key)
        overlap_notes: list[str] = []
        if company_key in lv_matches:
            overlap_notes.append("it appears in the License Verification violator overlap cohort")
        if company_key in broad_matches:
            overlap_notes.append("it appears in the broader Sales Routing Form violator overlap cohort")
        if not overlap_notes:
            overlap_notes.append("it is not in the current violator overlap cohorts")

        if metrics:
            metric_message = (
                f"SOLO shows {metrics.activations} activation row(s), "
                f"{metrics.rejected_activations} rejection(s), {metrics.unique_ips} unique IP(s), "
                f"{metrics.unique_installations} installation ID(s), and {metrics.deactivations} deactivation(s)."
            )
            evidence = metrics.__dict__
        else:
            metric_message = "I do not see activation metrics for that company in the current local SOLO files."
            evidence = {"company_key": company_key}

        canonical_name = (
            (lv_matches.get(company_key) or {}).get("company_name")
            or (metrics.company_name if metrics else company_name)
        )
        message = f"For `{canonical_name}`, " + " and ".join(overlap_notes) + f". {metric_message}"
        return DataQueryResult(
            kind="company_signal_lookup",
            message=message,
            evidence={
                "company_key": company_key,
                "license_verification_overlap": lv_matches.get(company_key),
                "broad_srf_overlap": company_key in broad_matches,
                "solo_metrics": evidence,
            },
        )

    def _answer_crm_lookup(self, text: str) -> DataQueryResult:
        company_name = extract_company_name(text) or _strip_crm_words(text)
        if not company_name:
            return DataQueryResult(
                kind="crm_lookup_needs_company",
                message="I can look up CRM account context once you include a company name.",
                evidence={"query": text},
            )
        lookup = self.aurora_client.search_company(company_name)
        if not lookup.get("configured"):
            return DataQueryResult(
                kind="crm_lookup_unconfigured",
                message=(
                    "The CRM lookup path is wired for read-only Aurora queries, but Aurora is not configured yet. "
                    "Set `AURORA_DATABASE_URL` plus the CRM account table/column settings, then this same chat "
                    f"question can look up `{company_name}` directly."
                ),
                evidence=lookup,
            )
        if lookup.get("error"):
            return DataQueryResult(
                kind="crm_lookup_error",
                message=f"I tried the Aurora CRM lookup for `{company_name}`, but it failed: {lookup['error']}",
                evidence=lookup,
            )
        rows = lookup.get("rows") or []
        if not rows:
            message = f"I did not find CRM account rows for `{company_name}` in Aurora."
        else:
            names = [str(row.get("company_name") or row.get("name") or row.get("account_name") or row) for row in rows[:3]]
            message = f"I found {len(rows)} CRM account row(s) for `{company_name}`. Top match(es): " + "; ".join(names)
        return DataQueryResult(kind="crm_lookup", message=message, evidence=lookup)

    def _answer_active_linktek_licenses(self, text: str) -> DataQueryResult:
        company_name = extract_company_name(text)
        license_text = extract_license_text(text)
        lookup = self.aurora_client.search_active_linktek_licenses(
            company_name=company_name,
            license_text=license_text,
        )
        if not lookup.get("configured"):
            return DataQueryResult(
                kind="active_linktek_licenses_unconfigured",
                message=(
                    "The active LinkTek license query path is wired for read-only Aurora, but Aurora is not configured yet. "
                    "Set `AURORA_DATABASE_URL` and the `AURORA_CRM_LICENSE_*` table mapping values."
                ),
                evidence=lookup,
            )
        if lookup.get("error"):
            return DataQueryResult(
                kind="active_linktek_licenses_error",
                message=f"I tried the Aurora active-license lookup, but it failed: {lookup['error']}",
                evidence=lookup,
            )
        rows = lookup.get("rows") or []
        subject = company_name or license_text or "the current filter"
        if not rows:
            message = f"I did not find active LinkTek licenses for `{subject}` in Aurora."
        else:
            names = [_license_row_label(row) for row in rows[:5]]
            message = f"I found {len(rows)} active LinkTek license row(s) for `{subject}`: " + "; ".join(names)
        return DataQueryResult(kind="active_linktek_licenses", message=message, evidence=lookup)

    def _answer_linked_active_license_records(self, text: str) -> DataQueryResult:
        company_name = extract_company_name(text)
        license_text = extract_license_text(text)
        lookup = self.aurora_client.search_active_linktek_licenses(
            company_name=company_name,
            license_text=license_text,
        )
        if not lookup.get("configured"):
            return DataQueryResult(
                kind="linked_records_unconfigured",
                message=(
                    "The linked-record lookup path is wired for read-only Aurora, but Aurora is not configured yet. "
                    "Set `AURORA_DATABASE_URL` and the CRM license/linked-table mapping values."
                ),
                evidence=lookup,
            )
        if lookup.get("error"):
            return DataQueryResult(
                kind="linked_records_error",
                message=f"I tried to find active LinkTek licenses first, but Aurora returned an error: {lookup['error']}",
                evidence=lookup,
            )
        licenses = lookup.get("rows") or []
        if not licenses:
            subject = company_name or license_text or "that request"
            return DataQueryResult(
                kind="linked_records",
                message=f"I did not find active LinkTek licenses for `{subject}`, so there were no linked records to fetch.",
                evidence={"license_lookup": lookup, "linked_records": []},
            )
        linked_payload = self.aurora_client.linked_records_for_active_licenses(licenses)
        if linked_payload.get("error"):
            return DataQueryResult(
                kind="linked_records_error",
                message=f"I found active licenses, but linked-record lookup failed: {linked_payload['error']}",
                evidence={"license_lookup": lookup, "linked_records": linked_payload},
            )
        total_linked = sum(
            len(records)
            for item in linked_payload.get("licenses", [])
            for records in (item.get("linked_records") or {}).values()
        )
        license_labels = ", ".join(_license_row_label(row) for row in licenses[:3])
        message = (
            f"I found {len(licenses)} active LinkTek license row(s) and {total_linked} linked CRM record(s). "
            f"License sample: {license_labels}."
        )
        return DataQueryResult(
            kind="linked_records",
            message=message,
            evidence={"license_lookup": lookup, "linked_records": linked_payload},
        )

    def _company_metrics(self, company_key: str):
        summary_metric = self._company_metrics_from_summary(company_key)
        if summary_metric is not None:
            return summary_metric
        activation_paths = find_all_csvs(self.solo_activation_root)
        if not activation_paths:
            return None
        rows = read_csv_rows_many(activation_paths, dedupe=True)
        metrics = build_company_metrics(rows)
        return metrics.get(company_key)

    def company_metrics_for_license_ids(self, license_ids: list[str]) -> SoloCompanyMetric | None:
        summary = self._load_solo_summary()
        if not summary:
            return None
        by_license_id = summary.get("by_license_id") or {}
        for license_id in license_ids:
            company_key = by_license_id.get(str(license_id))
            if company_key:
                metric = self._company_metrics_from_summary(str(company_key))
                if metric is not None:
                    return metric
        for company_key, payload in (summary.get("companies") or {}).items():
            if not isinstance(payload, dict):
                continue
            payload_license_ids = {str(value) for value in payload.get("license_ids") or []}
            if any(str(license_id) in payload_license_ids for license_id in license_ids):
                return self._company_metrics_from_summary(str(company_key))
        return None

    def _company_metrics_from_summary(self, company_key: str) -> SoloCompanyMetric | None:
        summary = self._load_solo_summary()
        if not summary:
            return None
        metric_payload = (summary.get("companies") or {}).get(company_key)
        if not isinstance(metric_payload, dict):
            return None
        try:
            return SoloCompanyMetric(
                company_key=str(metric_payload.get("company_key") or company_key),
                company_name=str(metric_payload.get("company_name") or company_key),
                activations=int(metric_payload.get("activations") or 0),
                successful_activations=int(metric_payload.get("successful_activations") or 0),
                rejected_activations=int(metric_payload.get("rejected_activations") or 0),
                rejection_rate=float(metric_payload.get("rejection_rate") or 0),
                unique_ips=int(metric_payload.get("unique_ips") or 0),
                unique_installations=int(metric_payload.get("unique_installations") or 0),
                unique_computers=int(metric_payload.get("unique_computers") or 0),
                deactivations=int(metric_payload.get("deactivations") or 0),
                unique_licenses=int(metric_payload.get("unique_licenses") or 0),
                q_ordered_total=int(metric_payload.get("q_ordered_total") or 0),
                q_ordered_max=int(metric_payload.get("q_ordered_max") or 0),
                q_ordered_license_count=int(metric_payload.get("q_ordered_license_count") or 0),
                solo_entitlement_count=_int_or_none(metric_payload.get("solo_entitlement_count")),
                solo_entitlement_source=str(metric_payload.get("solo_entitlement_source") or ""),
                license_ids=tuple(str(value) for value in metric_payload.get("license_ids") or []),
                activation_ips=tuple(str(value) for value in metric_payload.get("activation_ips") or []),
            )
        except (TypeError, ValueError):
            return None

    def _load_solo_summary(self) -> dict[str, Any] | None:
        if self.settings.solo_summary_local_path:
            path = Path(self.settings.solo_summary_local_path)
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
        if self.settings.raw_s3_bucket and self.settings.solo_summary_s3_key:
            try:
                import boto3

                client = boto3.client("s3", region_name=self.settings.aws_region)
                response = client.get_object(
                    Bucket=self.settings.raw_s3_bucket,
                    Key=self.settings.solo_summary_s3_key,
                )
                return json.loads(response["Body"].read().decode("utf-8"))
            except Exception:
                return None
        return None

    def _solo_summary_status(self) -> dict[str, Any]:
        return {
            "configured": bool(self.settings.solo_summary_local_path or self.settings.raw_s3_bucket),
            "local_path": self.settings.solo_summary_local_path,
            "s3_bucket": self.settings.raw_s3_bucket,
            "s3_key": self.settings.solo_summary_s3_key,
        }

    def _load_latest_cohort_report(self) -> dict[str, Any] | None:
        report_path = self._latest_cohort_report()
        if report_path is None:
            return None
        return json.loads(report_path.read_text(encoding="utf-8"))

    def _latest_cohort_report(self) -> Path | None:
        candidates = sorted(self.analysis_root.glob("*/cohort_report.json"))
        return candidates[-1] if candidates else None

    def _missing_report_result(self) -> DataQueryResult:
        return DataQueryResult(
            kind="missing_analysis",
            message="I do not have a SOLO signal review report available yet. Run `scripts/analyze_solo_exports.py` first.",
            evidence={"analysis_root": str(self.analysis_root)},
        )


class AuroraReadOnlyClient:
    """Small read-only Aurora adapter with an explicit table/column allowlist."""

    def __init__(self, settings: LicenseAgentSettings, *, rds_data_client: Any | None = None) -> None:
        self.settings = settings
        self.rds_data_client = rds_data_client

    def status(self) -> dict[str, Any]:
        data_api_configured = self._data_api_configured()
        return {
            "configured": bool(self.settings.aurora_database_url) or data_api_configured,
            "connection_mode": "rds_data_api" if data_api_configured else "postgres_url",
            "data_api_configured": data_api_configured,
            "driver_available": self._driver_name() is not None,
            "schema": self.settings.aurora_crm_schema,
            "accounts_table": self.settings.aurora_crm_accounts_table,
            "company_name_column": self.settings.aurora_crm_company_name_column,
            "licenses_table": self.settings.aurora_crm_licenses_table,
            "license_id_column": self.settings.aurora_crm_license_id_column,
            "license_code_column": self.settings.aurora_crm_license_code_column,
            "license_company_column": self.settings.aurora_crm_license_company_column,
            "license_entity_column": self.settings.aurora_crm_license_entity_column,
            "license_active_column": self.settings.aurora_crm_license_active_column,
            "license_expiry_column": self.settings.aurora_crm_license_expiry_column,
            "linktek_entity_value": self.settings.aurora_crm_linktek_entity_value,
            "linked_tables": [config.name for config in self._linked_record_configs()],
        }

    def search_company(self, company_name: str, *, limit: int = 5) -> dict[str, Any]:
        status = self.status()
        if not status["configured"]:
            return {**status, "rows": [], "error": ""}
        if status["data_api_configured"]:
            return self._search_company_data_api(company_name, limit=limit, status=status)
        driver_name = self._driver_name()
        if driver_name is None:
            return {**status, "rows": [], "error": "Neither psycopg nor psycopg2 is installed."}
        table = _safe_identifier(self.settings.aurora_crm_accounts_table)
        schema = _safe_identifier(self.settings.aurora_crm_schema)
        column = _safe_identifier(self.settings.aurora_crm_company_name_column)
        if not table or not schema or not column:
            return {**status, "rows": [], "error": "Aurora CRM table mapping contains an unsafe identifier."}
        sql = (
            f'SELECT * FROM "{schema}"."{table}" '
            f'WHERE "{column}" ILIKE %s '
            f'ORDER BY "{column}" ASC '
            "LIMIT %s"
        )
        try:
            if driver_name == "psycopg":
                rows = self._search_company_psycopg(sql, company_name, limit)
            else:
                rows = self._search_company_psycopg2(sql, company_name, limit)
            return {**status, "rows": rows, "error": ""}
        except Exception as exc:  # pragma: no cover - requires live Aurora
            return {**status, "rows": [], "error": f"{type(exc).__name__}: {exc}"}

    def search_active_linktek_licenses(
        self,
        *,
        company_name: str | None = None,
        license_text: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        return self.search_linktek_licenses(
            company_name=company_name,
            license_text=license_text,
            limit=limit,
            active_only=True,
        )

    def search_linktek_licenses(
        self,
        *,
        company_name: str | None = None,
        license_text: str | None = None,
        limit: int = 10,
        active_only: bool = True,
    ) -> dict[str, Any]:
        status = self.status()
        if not status["configured"]:
            return {**status, "rows": [], "error": ""}
        if status["data_api_configured"]:
            return self._search_linktek_licenses_data_api(
                company_name=company_name,
                license_text=license_text,
                limit=limit,
                active_only=active_only,
                status=status,
            )
        table = _safe_identifier(self.settings.aurora_crm_licenses_table)
        schema = _safe_identifier(self.settings.aurora_crm_schema)
        id_col = _safe_identifier(self.settings.aurora_crm_license_id_column)
        code_col = _safe_identifier(self.settings.aurora_crm_license_code_column)
        company_col = _safe_identifier(self.settings.aurora_crm_license_company_column)
        entity_col = _safe_identifier(self.settings.aurora_crm_license_entity_column)
        active_col = _safe_identifier(self.settings.aurora_crm_license_active_column)
        expiry_col = _safe_identifier(self.settings.aurora_crm_license_expiry_column)
        if not all((table, schema, id_col, code_col, company_col, entity_col, active_col, expiry_col)):
            return {**status, "rows": [], "error": "Aurora CRM license table mapping contains an unsafe identifier."}

        where = [
            f'"{entity_col}" ILIKE %s',
        ]
        params: list[Any] = [self.settings.aurora_crm_linktek_entity_value]
        if active_only:
            where.extend(
                [
                    f'LOWER("{active_col}"::text) IN (\'true\', \'1\', \'yes\')',
                    f'NULLIF("{expiry_col}"::text, \'\')::date > CURRENT_DATE',
                ]
            )
        if company_name:
            where.append(f'"{company_col}" ILIKE %s')
            params.append(f"%{company_name}%")
        if license_text:
            where.append(f'("{id_col}"::text = %s OR "{code_col}"::text = %s)')
            params.extend([license_text, license_text])
        params.append(limit)
        sql = (
            f'SELECT * FROM "{schema}"."{table}" '
            f"WHERE {' AND '.join(where)} "
            f'ORDER BY "{company_col}" ASC, "{expiry_col}" ASC '
            "LIMIT %s"
        )
        try:
            rows = self._execute_query(sql, tuple(params))
            return {**status, "rows": rows, "error": ""}
        except Exception as exc:  # pragma: no cover - requires live Aurora
            return {**status, "rows": [], "error": f"{type(exc).__name__}: {exc}"}

    def linked_records_for_active_licenses(
        self,
        license_rows: list[dict[str, Any]],
        *,
        per_table_limit: int = 5,
    ) -> dict[str, Any]:
        status = self.status()
        if not status["configured"]:
            return {**status, "licenses": [], "error": ""}
        if status["data_api_configured"]:
            return self._linked_records_for_active_licenses_data_api(
                license_rows,
                per_table_limit=per_table_limit,
                status=status,
            )
        schema = _safe_identifier(self.settings.aurora_crm_schema)
        if not schema:
            return {**status, "licenses": [], "error": "Aurora CRM schema contains an unsafe identifier."}
        configs = self._linked_record_configs()
        payload: list[dict[str, Any]] = []
        try:
            for license_row in license_rows:
                linked_records: dict[str, list[dict[str, Any]]] = {}
                for config in configs:
                    table = _safe_identifier(config.table)
                    target_column = _safe_identifier(config.target_column)
                    if not table or not target_column:
                        return {**status, "licenses": [], "error": f"Unsafe linked-record mapping for {config.name}."}
                    source_value = _source_value_for_license(license_row, config.source_column)
                    if not source_value:
                        linked_records[config.name] = []
                        continue
                    if config.match_mode == "contains":
                        predicate = f'"{target_column}"::text ILIKE %s'
                        params = (f"%{source_value}%", per_table_limit)
                    else:
                        predicate = f'"{target_column}"::text = %s'
                        params = (source_value, per_table_limit)
                    sql = (
                        f'SELECT * FROM "{schema}"."{table}" '
                        f"WHERE {predicate} "
                        "LIMIT %s"
                    )
                    linked_records[config.name] = self._execute_query(sql, params)
                payload.append({"license": license_row, "linked_records": linked_records})
            return {**status, "licenses": payload, "error": ""}
        except Exception as exc:  # pragma: no cover - requires live Aurora
            return {**status, "licenses": payload, "error": f"{type(exc).__name__}: {exc}"}

    def _search_company_data_api(self, company_name: str, *, limit: int, status: dict[str, Any]) -> dict[str, Any]:
        sql = """
            SELECT 'company' AS record_type, c.id, c."Name" AS company_name, NULL AS account_name,
                   c.entity, c."Owner"->>'name' AS owner_name, c.last_worked::text AS last_worked
            FROM zoho.companies c
            WHERE c."Name" ILIKE :query AND NOT c._is_deleted
            UNION ALL
            SELECT 'site' AS record_type, s.id, s.company_name, s.account_name,
                   s.entity, s."Owner"->>'name' AS owner_name, s.last_worked::text AS last_worked
            FROM zoho.sites s
            WHERE (s.company_name ILIKE :query OR s.account_name ILIKE :query)
              AND NOT s._is_deleted
            ORDER BY company_name NULLS LAST, account_name NULLS LAST
            LIMIT :limit
        """
        try:
            rows = self._execute_data_api_query(sql, {"query": f"%{company_name}%", "limit": limit})
            return {**status, "rows": rows, "error": ""}
        except Exception as exc:  # pragma: no cover - live Aurora
            return {**status, "rows": [], "error": f"{type(exc).__name__}: {exc}"}

    def _search_linktek_licenses_data_api(
        self,
        *,
        company_name: str | None,
        license_text: str | None,
        limit: int,
        active_only: bool,
        status: dict[str, Any],
    ) -> dict[str, Any]:
        where = [
            "cl.entity = :entity",
            "NOT cl._is_deleted",
        ]
        if active_only:
            where.extend(
                [
                    "cl.active_license",
                    "cl.maintenance_expiry_date > CURRENT_DATE",
                ]
            )
        params: dict[str, Any] = {"entity": self.settings.aurora_crm_linktek_entity_value, "limit": limit}
        if company_name:
            where.append(
                """(
                    c."Name" ILIKE :company_query
                    OR s.company_name ILIKE :company_query
                    OR s.account_name ILIKE :company_query
                    OR cl.company->>'name' ILIKE :company_query
                    OR cl.site_code->>'name' ILIKE :company_query
                )"""
            )
            params["company_query"] = f"%{company_name}%"
        if license_text:
            where.append(
                """(
                    cl.id = :license_text
                    OR cl."Name" ILIKE :license_like
                    OR cl.license_code ILIKE :license_like
                    OR cl.serial_number ILIKE :license_like
                    OR cl.gm_serial_number ILIKE :license_like
                    OR cl.qlm_license_key ILIKE :license_like
                )"""
            )
            params["license_text"] = license_text
            params["license_like"] = f"%{license_text}%"
        sql = f"""
            SELECT cl.id,
                   cl."Name" AS name,
                   cl.license_code,
                   cl.password IS NOT NULL AND cl.password <> '' AS solo_password_present,
                   cl.serial_number,
                   cl.gm_serial_number,
                   cl.qlm_license_key,
                   cl.company->>'name' AS company,
                   COALESCE(c."Name", cl.company->>'name') AS company_name,
                   cl.company->>'id' AS company_id,
                   cl.site_code->>'name' AS site,
                   cl.site_code->>'id' AS site_id,
                   cl.product->>'name' AS product,
                   cl.active_license,
                   cl.maintenance_expiry_date::text AS maintenance_expiry_date,
                   cl.links AS link_limit,
                   cl.estimated_personnel_count,
                   cl.employee_or_computer_count,
                   cl.total_seat_count,
                   cl.site_count,
                   cl.single_quantity,
                   cl.subset_license_count,
                   cl.subset_price_multiplier_3,
                   cl.entire_legal_entity_personnel_count_3,
                   cl.entire_legal_entity_price_multiplier_3,
                   cl.number_of_activations_allowed,
                   cl.which_count_to_use,
                   cl.organization_description,
                   cl.possible_violation,
                   cl.date_last_checked::text AS date_last_checked
            FROM zoho.customer_licenses cl
            LEFT JOIN zoho.sites s ON cl.site_code->>'id' = s.id AND NOT s._is_deleted
            LEFT JOIN zoho.companies c ON cl.company->>'id' = c.id AND NOT c._is_deleted
            WHERE {' AND '.join(where)}
            ORDER BY cl.maintenance_expiry_date DESC NULLS LAST, company_name ASC
            LIMIT :limit
        """
        try:
            rows = self._execute_data_api_query(sql, params)
            return {**status, "rows": rows, "error": ""}
        except Exception as exc:  # pragma: no cover - live Aurora
            return {**status, "rows": [], "error": f"{type(exc).__name__}: {exc}"}

    def _linked_records_for_active_licenses_data_api(
        self,
        license_rows: list[dict[str, Any]],
        *,
        per_table_limit: int,
        status: dict[str, Any],
    ) -> dict[str, Any]:
        payload: list[dict[str, Any]] = []
        try:
            for license_row in license_rows:
                license_id = str(license_row.get("id") or "")
                site_id = str(license_row.get("site_id") or "")
                linked_records: dict[str, list[dict[str, Any]]] = {
                    "license_verifications": [],
                    "qlm_license_keys": [],
                    "quote_line_item_sets": [],
                    "sales_routing_forms": [],
                    "deals": [],
                    "notes": [],
                }
                if license_id:
                    linked_records["license_verifications"] = self._execute_data_api_query(
                        """
                        SELECT lv.id, lv."Name" AS name, lv.stage, lv.current_status,
                               lv.organization_definition, lv.personnel_count,
                               lv.estimated_personnel_count, lv.potential_gross_value,
                               lv.reported_date::text AS reported_date,
                               lv.purchase_date::text AS purchase_date,
                               lv.maintenance_expiration_date::text AS maintenance_expiration_date,
                               lv.closed_date::text AS closed_date
                        FROM zoho.license_verifications lv
                        WHERE lv.existing_license_record->>'id' = :license_id
                          AND NOT lv._is_deleted
                        ORDER BY lv.reported_date DESC NULLS LAST
                        LIMIT :limit
                        """,
                        {"license_id": license_id, "limit": per_table_limit},
                    )
                    linked_records["qlm_license_keys"] = self._execute_data_api_query(
                        """
                        SELECT k.id, k."Name" AS name, k.qlm_license_with_prefix,
                               k.available_activations, k.maintenance_expiry_date::text AS maintenance_expiry_date
                        FROM zoho.qlm_license_keys k
                        WHERE k.linked_customer_license_record->>'id' = :license_id
                          AND NOT k._is_deleted
                        LIMIT :limit
                        """,
                        {"license_id": license_id, "limit": per_table_limit},
                    )
                    qli_table = _safe_identifier(self.settings.aurora_crm_quote_line_items_table)
                    qli_column = _safe_identifier(self.settings.aurora_crm_quote_line_items_license_column)
                    schema = _safe_identifier(self.settings.aurora_crm_schema)
                    if qli_table and qli_column and schema:
                        linked_records["quote_line_item_sets"] = self._execute_data_api_query(
                            f"""
                            SELECT qli.*
                            FROM "{schema}"."{qli_table}" qli
                            WHERE (
                                qli."{qli_column}"->>'id' = :license_id
                                OR qli."{qli_column}"::text ILIKE :license_like
                            )
                              AND NOT qli._is_deleted
                            LIMIT :limit
                            """,
                            {"license_id": license_id, "license_like": f"%{license_id}%", "limit": per_table_limit},
                        )
                    linked_records["notes"] = self._execute_data_api_query(
                        """
                        SELECT n.id, n.note_title, LEFT(n.note_content, 500) AS note_content,
                               n."Owner"->>'name' AS owner_name, n.created_time::text AS created_time,
                               n._parent_module
                        FROM zoho.notes n
                        WHERE n.parent_id->>'id' = :license_id
                          AND NOT n._is_deleted
                        ORDER BY n.created_time DESC NULLS LAST
                        LIMIT :limit
                        """,
                        {"license_id": license_id, "limit": per_table_limit},
                    )
                if site_id:
                    linked_records["sales_routing_forms"] = self._execute_data_api_query(
                        """
                        SELECT srf.id, srf."Name" AS name, srf."Owner"->>'name' AS owner_name,
                               srf.created_time::text AS created_time,
                               srf.software_gross_income, srf.maintenance_gross_income,
                               srf.total_gross, srf.license_violation, srf.unresolved_license_violation
                        FROM zoho.sales_routing_forms srf
                        WHERE srf.site->>'id' = :site_id
                          AND NOT srf._is_deleted
                        ORDER BY srf.created_time DESC NULLS LAST
                        LIMIT :limit
                        """,
                        {"site_id": site_id, "limit": per_table_limit},
                    )
                    linked_records["deals"] = self._execute_data_api_query(
                        """
                        SELECT d.id, d.deal_name AS name, d.stage, d.entity,
                               d.amount, d.renewal_due_date::text AS renewal_due_date,
                               d."Owner"->>'name' AS owner_name
                        FROM zoho.deals d
                        WHERE d.account_name->>'id' = :site_id
                          AND NOT d._is_deleted
                        ORDER BY d.created_time DESC NULLS LAST
                        LIMIT :limit
                        """,
                        {"site_id": site_id, "limit": per_table_limit},
                    )
                payload.append({"license": license_row, "linked_records": linked_records})
            return {**status, "licenses": payload, "error": ""}
        except Exception as exc:  # pragma: no cover - live Aurora
            return {**status, "licenses": payload, "error": f"{type(exc).__name__}: {exc}"}

    def _data_api_configured(self) -> bool:
        return bool(
            self.settings.aurora_data_api_cluster_arn
            and self.settings.aurora_data_api_secret_arn
            and self.settings.aurora_data_api_database
        )

    def _execute_data_api_query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        client = self.rds_data_client or self._rds_data_client()
        statement_params = [
            {"name": name, "value": _data_api_value(value)}
            for name, value in params.items()
        ]
        waited = 0
        while True:
            try:
                result = client.execute_statement(
                    resourceArn=self.settings.aurora_data_api_cluster_arn,
                    secretArn=self.settings.aurora_data_api_secret_arn,
                    database=self.settings.aurora_data_api_database,
                    sql=" ".join(sql.strip().split()),
                    parameters=statement_params,
                    includeResultMetadata=True,
                )
                return _parse_data_api_rows(result)
            except Exception as exc:
                code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
                if code == "DatabaseResumingException" and waited < 42:
                    time.sleep(3)
                    waited += 3
                    continue
                raise

    def _rds_data_client(self):
        import boto3

        return boto3.client("rds-data", region_name=self.settings.aws_region)

    def _search_company_psycopg(self, sql: str, company_name: str, limit: int) -> list[dict[str, Any]]:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self.settings.aurora_database_url, row_factory=dict_row) as conn:
            conn.read_only = True
            with conn.cursor() as cursor:
                cursor.execute(sql, (f"%{company_name}%", limit))
                return [dict(row) for row in cursor.fetchall()]

    def _search_company_psycopg2(self, sql: str, company_name: str, limit: int) -> list[dict[str, Any]]:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        with psycopg2.connect(self.settings.aurora_database_url) as conn:
            conn.set_session(readonly=True, autocommit=True)
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(sql, (f"%{company_name}%", limit))
                return [dict(row) for row in cursor.fetchall()]

    def _execute_query(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        driver_name = self._driver_name()
        if driver_name is None:
            raise RuntimeError("Neither psycopg nor psycopg2 is installed.")
        if driver_name == "psycopg":
            return self._execute_query_psycopg(sql, params)
        return self._execute_query_psycopg2(sql, params)

    def _execute_query_psycopg(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self.settings.aurora_database_url, row_factory=dict_row) as conn:
            conn.read_only = True
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                return [dict(row) for row in cursor.fetchall()]

    def _execute_query_psycopg2(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        import psycopg2
        from psycopg2.extras import RealDictCursor

        with psycopg2.connect(self.settings.aurora_database_url) as conn:
            conn.set_session(readonly=True, autocommit=True)
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(sql, params)
                return [dict(row) for row in cursor.fetchall()]

    def _driver_name(self) -> str | None:
        if importlib.util.find_spec("psycopg") is not None:
            return "psycopg"
        if importlib.util.find_spec("psycopg2") is not None:
            return "psycopg2"
        return None

    def _linked_record_configs(self) -> list["LinkedRecordConfig"]:
        return [
            LinkedRecordConfig(
                name="sales_routing_forms",
                table=self.settings.aurora_crm_srf_table,
                target_column=self.settings.aurora_crm_srf_license_column,
                source_column=self.settings.aurora_crm_license_code_column,
                match_mode="exact",
            ),
            LinkedRecordConfig(
                name="license_verifications",
                table=self.settings.aurora_crm_license_verifications_table,
                target_column=self.settings.aurora_crm_license_verifications_license_column,
                source_column=self.settings.aurora_crm_license_id_column,
                match_mode="contains",
            ),
            LinkedRecordConfig(
                name="quote_line_item_sets",
                table=self.settings.aurora_crm_quote_line_items_table,
                target_column=self.settings.aurora_crm_quote_line_items_license_column,
                source_column=self.settings.aurora_crm_license_id_column,
                match_mode="contains",
            ),
        ]


@dataclass(frozen=True)
class LinkedRecordConfig:
    name: str
    table: str
    target_column: str
    source_column: str
    match_mode: str


def _data_api_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"isNull": True}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"longValue": value}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": str(value)}


def _parse_data_api_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    columns = [meta.get("name", "") for meta in result.get("columnMetadata", [])]
    rows: list[dict[str, Any]] = []
    for record in result.get("records", []):
        row: dict[str, Any] = {}
        for index, field in enumerate(record):
            column = columns[index] if index < len(columns) else f"column_{index}"
            row[column] = _data_api_field_value(field)
        rows.append(row)
    return rows


def _data_api_field_value(field: dict[str, Any]) -> Any:
    if field.get("isNull"):
        return None
    for key in ("stringValue", "longValue", "doubleValue", "booleanValue", "blobValue", "arrayValue"):
        if key in field:
            return field[key]
    return None


def looks_like_data_query(text: str) -> bool:
    lower = text.lower()
    return (
        _asks_about_usage_activity(lower)
        or _asks_about_active_linktek_licenses(lower)
        or _asks_about_linked_license_records(lower)
        or _asks_about_crm(lower)
        or _asks_about_dataset(lower)
        or _asks_about_signals(lower)
        or bool(re.search(r"\b(?:is|was|were|show|lookup|look up|find)\b.+\b(?:violator|violation|overlap|crm|database|customer)\b", lower))
    )


def extract_company_name(text: str) -> str | None:
    patterns = (
        r"\bhow\s+many\s+(?:files|links)\s+(?:did\s+)?(.+?)\s+(?:actually\s+)?(?:ran|run|processed)\b",
        r"\b(?:files|links|usage|processes|runs?)\s+(?:for|by)\s+(.+?)(?:[?.!]*)$",
        r"\blicenses?\s+for\s+(.+?)(?:\s+in\s+crm|\s+in\s+the\s+crm|\s+in\s+aurora|\s+violator|\s+violation|\s+overlap|[?.!]*)$",
        r"\b(?:for|on|about|company|customer|account)\s+(.+?)(?:\s+in\s+crm|\s+in\s+the\s+crm|\s+in\s+aurora|\s+violator|\s+violation|\s+overlap|[?.!]*)$",
        r"\b(?:is|was)\s+(.+?)\s+(?:a\s+)?(?:violator|in\s+the\s+violator\s+overlap|in\s+the\s+overlap)",
        r"\b(?:look up|lookup|find|show)\s+(.+?)(?:\s+in\s+crm|\s+in\s+the\s+crm|\s+in\s+aurora|[?.!]*)$",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = _clean_company_name_fragment(match.group(1))
            if value:
                return value
    return None


def extract_usage_company_name(text: str) -> str | None:
    patterns = (
        r"\bhow\s+many\s+(?:files|links)\s+(.+?)\s+(?:actually\s+)?(?:ran|run|processed)\b",
        r"\bwhat\s+did\s+(.+?)\s+(?:run|process)\b",
        r"\b(?:usage|files|links|processes|runs?)\s+(?:for|by)\s+(.+?)(?:[?.!]*)$",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = _clean_company_name_fragment(match.group(1))
            if value:
                return value
    return None


def _asks_about_crm(lower: str) -> bool:
    return "crm" in lower or "aurora" in lower or "zoho" in lower


def _asks_about_active_linktek_licenses(lower: str) -> bool:
    return "active" in lower and "license" in lower and ("linktek" in lower or "customer" in lower or "company" in lower)


def _asks_about_linked_license_records(lower: str) -> bool:
    linked_words = ("linked", "related", "associated", "attached", "records")
    return "license" in lower and any(word in lower for word in linked_words)


def _asks_about_dataset(lower: str) -> bool:
    phrases = ("how much data", "how many licenses", "dataset", "data set", "data coverage", "what data")
    return any(phrase in lower for phrase in phrases)


def _asks_about_signals(lower: str) -> bool:
    phrases = ("signal", "signals", "outpoint", "outpoints", "correlation", "correlate", "predictive")
    return any(phrase in lower for phrase in phrases)


def _asks_about_usage_activity(lower: str) -> bool:
    usage_terms = ("files", "links", "bytes", "usage", "processed", "processes", "ran", "run")
    context_terms = ("how many", "how much", "actually", "machine", "mac address", "tenant", "site")
    return any(term in lower for term in usage_terms) and any(term in lower for term in context_terms)


def _strip_crm_words(text: str) -> str:
    cleaned = re.sub(r"\b(?:crm|aurora|zoho|database|look up|lookup|find|show|account|customer)\b", "", text, flags=re.IGNORECASE)
    return _clean_company_name_fragment(cleaned)


def _clean_company_name_fragment(value: str) -> str:
    cleaned = value.strip(" .?!,;:`'\"")
    cleaned = re.sub(r"^(do\s+you\s+have\s+data\s+on\s+)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(the\s+company\s+|company\s+|customer\s+|account\s+)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _safe_identifier(value: str | None) -> str | None:
    if not value:
        return None
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        return None
    return value


def extract_license_text(text: str) -> str | None:
    match = re.search(
        r"\b(?:license\s+id|license\s+code|license|lic)\b\s*[:#]?\s*([A-Za-z0-9._-]{4,})\b",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        return match.group(1)
    return None


def _license_row_label(row: dict[str, Any]) -> str:
    company = row.get("company") or row.get("company_name") or row.get("account_name") or "unknown company"
    code = row.get("license_code") or row.get("serial_number") or row.get("id") or "unknown license"
    expiry = row.get("maintenance_expiry_date") or row.get("expiration_date") or "unknown expiry"
    product = row.get("product") or row.get("Name") or row.get("name") or "license"
    return f"{company} / {code} / {product} / expires {expiry}"


def _usage_summary_message(summary: dict[str, Any], *, requested_company: str) -> str:
    company_name = summary.get("company_name") or requested_company
    files_processed = _format_int(summary.get("files_processed"))
    links_processed = _format_int(summary.get("links_processed"))
    file_size_gib = summary.get("file_size_gib")
    run_count = _format_int(summary.get("run_count"))
    license_count = len(summary.get("license_ids") or [])
    machine_count = _format_int(summary.get("machine_count"))
    mac_count = _format_int(summary.get("mac_count"))
    first_start = summary.get("first_start_time") or "unknown"
    last_end = summary.get("last_end_time") or "unknown"
    tasks = ", ".join((summary.get("tasks") or [])[:5]) or "unknown tasks"
    return (
        f"I found AWS ProcessInfo usage for `{company_name}`. It shows {files_processed} file(s) processed, "
        f"{links_processed} link(s) processed, and about {file_size_gib} GiB of file volume across {run_count} "
        f"run row(s). I see {license_count} license ID(s), {machine_count} machine name(s), and {mac_count} "
        f"MAC address(es). Date range: {first_start} through {last_end}. Process/task sample: {tasks}."
    )


def _format_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _source_value_for_license(license_row: dict[str, Any], source_column: str) -> str:
    value = license_row.get(source_column)
    if value is None:
        lowered = {str(key).lower(): value for key, value in license_row.items()}
        value = lowered.get(source_column.lower())
    return str(value or "").strip()
