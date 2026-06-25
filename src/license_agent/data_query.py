from __future__ import annotations

import importlib.util
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .correlation_analysis import normalize_company_name
from .settings import LicenseAgentSettings
from .solo_analysis import build_company_metrics, find_all_csvs, read_csv_rows_many


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
    ) -> None:
        self.settings = settings
        self.analysis_root = Path(analysis_root)
        self.solo_activation_root = Path(solo_activation_root)
        self.aurora_client = aurora_client or AuroraReadOnlyClient(settings)

    def runtime_status(self) -> dict[str, Any]:
        return {
            "latest_solo_signal_report": str(self._latest_cohort_report() or ""),
            "aurora": self.aurora_client.status(),
        }

    def answer(self, text: str) -> DataQueryResult:
        lower = text.lower()
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
                "I can answer questions about the SOLO signal review, violator overlap, dataset coverage, "
                "and CRM company lookup. Try asking `what are the strongest violation signals?` or "
                "`is Hudson Housing Capital LLC in the violator overlap?`."
            ),
            evidence={"query": text},
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
        activation_paths = find_all_csvs(self.solo_activation_root)
        if not activation_paths:
            return None
        rows = read_csv_rows_many(activation_paths, dedupe=True)
        metrics = build_company_metrics(rows)
        return metrics.get(company_key)

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

    def __init__(self, settings: LicenseAgentSettings) -> None:
        self.settings = settings

    def status(self) -> dict[str, Any]:
        return {
            "configured": bool(self.settings.aurora_database_url),
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
        status = self.status()
        if not status["configured"]:
            return {**status, "rows": [], "error": ""}
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
            f'LOWER("{active_col}"::text) IN (\'true\', \'1\', \'yes\')',
            f'NULLIF("{expiry_col}"::text, \'\')::date > CURRENT_DATE',
        ]
        params: list[Any] = [self.settings.aurora_crm_linktek_entity_value]
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


def looks_like_data_query(text: str) -> bool:
    lower = text.lower()
    return (
        _asks_about_active_linktek_licenses(lower)
        or _asks_about_linked_license_records(lower)
        or _asks_about_crm(lower)
        or _asks_about_dataset(lower)
        or _asks_about_signals(lower)
        or bool(re.search(r"\b(?:is|was|were|show|lookup|look up|find)\b.+\b(?:violator|violation|overlap|crm|database|customer)\b", lower))
    )


def extract_company_name(text: str) -> str | None:
    patterns = (
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


def _strip_crm_words(text: str) -> str:
    cleaned = re.sub(r"\b(?:crm|aurora|zoho|database|look up|lookup|find|show|account|customer)\b", "", text, flags=re.IGNORECASE)
    return _clean_company_name_fragment(cleaned)


def _clean_company_name_fragment(value: str) -> str:
    cleaned = value.strip(" .?!,;:`'\"")
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


def _source_value_for_license(license_row: dict[str, Any], source_column: str) -> str:
    value = license_row.get(source_column)
    if value is None:
        lowered = {str(key).lower(): value for key, value in license_row.items()}
        value = lowered.get(source_column.lower())
    return str(value or "").strip()
