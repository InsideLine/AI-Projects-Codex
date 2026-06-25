from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .aws_cli import AwsCliError, fetch_secret_json, get_aws_cli_info


def load_dotenv(path: str | Path) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class LicenseAgentSettings:
    aws_region: str = "us-east-1"
    aws_profile: str | None = None
    aws_cli_path: str | None = None
    ingest_raw_root: str = "local_data/raw"
    app_db_path: str = "local_data/app/license_agent.sqlite3"
    report_output_root: str = "local_data/reports"
    chat_store_backend: str = "sqlite"
    chat_state_table_name: str | None = None
    teams_shared_secret: str | None = None
    teams_app_secret_name: str | None = None
    microsoft_app_id: str | None = None
    microsoft_app_password: str | None = None
    microsoft_app_type: str = "SingleTenant"
    microsoft_app_tenant_id: str | None = None
    raw_s3_bucket: str | None = None
    report_s3_bucket: str | None = None
    athena_output_s3_uri: str | None = None
    glue_database_name: str | None = None
    aurora_database_url: str | None = None
    aurora_crm_schema: str = "public"
    aurora_crm_accounts_table: str = "accounts"
    aurora_crm_company_name_column: str = "company_name"
    aurora_crm_licenses_table: str = "customer_licenses"
    aurora_crm_license_id_column: str = "id"
    aurora_crm_license_code_column: str = "license_code"
    aurora_crm_license_company_column: str = "company"
    aurora_crm_license_entity_column: str = "entity"
    aurora_crm_license_active_column: str = "active_license"
    aurora_crm_license_expiry_column: str = "maintenance_expiry_date"
    aurora_crm_linktek_entity_value: str = "LinkTek"
    aurora_crm_srf_table: str = "sales_routing_forms"
    aurora_crm_srf_license_column: str = "license_id"
    aurora_crm_license_verifications_table: str = "license_verifications"
    aurora_crm_license_verifications_license_column: str = "existing_license_record"
    aurora_crm_quote_line_items_table: str = "quote_line_item_sets"
    aurora_crm_quote_line_items_license_column: str = "customer_license_record"
    dynamodb_source_tables: str = "ProcessInfo,SiteInfo,TenantInfo"
    dynamodb_source_system: str = "aws_dynamodb"
    dynamodb_scan_page_limit: int = 1000
    dynamodb_parallel_segments: int = 8
    dynamodb_source_role_arn: str | None = None
    dynamodb_source_external_id: str | None = None
    solo_credentials_secret_name: str | None = None
    solo_base_url: str = "https://secure.softwarekey.com/solo"
    solo_author_id: str | None = None
    solo_api_user_id: str | None = None
    solo_api_user_password: str | None = None
    zoho_credentials_secret_name: str | None = None
    zoho_datacenter: str = "com"
    zoho_client_id: str | None = None
    zoho_client_secret: str | None = None
    zoho_refresh_token: str | None = None
    zoho_grant_token: str | None = None
    zoho_redirect_uri: str | None = None
    zoho_scopes: str = "ZohoCRM.modules.ALL"
    zoho_crm_api_base: str | None = None
    zoho_analytics_workspace_name: str = "Statistics Pilot"
    zoho_analytics_workspace_id: str | None = None
    zoho_analytics_org_id: str | None = None

    @classmethod
    def from_env(
        cls,
        dotenv_path: str | Path | None = None,
        *,
        enable_secret_fallback: bool = True,
    ) -> "LicenseAgentSettings":
        if dotenv_path:
            load_dotenv(dotenv_path)

        settings = cls(
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
            aws_profile=_clean(os.getenv("AWS_PROFILE")),
            aws_cli_path=_clean(os.getenv("AWS_CLI_PATH")),
            ingest_raw_root=os.getenv("INGEST_RAW_ROOT", "local_data/raw"),
            app_db_path=os.getenv("APP_DB_PATH", "local_data/app/license_agent.sqlite3"),
            report_output_root=os.getenv("REPORT_OUTPUT_ROOT", "local_data/reports"),
            chat_store_backend=os.getenv("CHAT_STORE_BACKEND", "sqlite"),
            chat_state_table_name=_clean(os.getenv("CHAT_STATE_TABLE_NAME")),
            teams_shared_secret=_clean(os.getenv("TEAMS_SHARED_SECRET")),
            teams_app_secret_name=_clean(os.getenv("TEAMS_APP_SECRET_NAME")),
            microsoft_app_id=_clean(os.getenv("MICROSOFT_APP_ID")),
            microsoft_app_password=_clean(os.getenv("MICROSOFT_APP_PASSWORD")),
            microsoft_app_type=os.getenv("MICROSOFT_APP_TYPE", "SingleTenant"),
            microsoft_app_tenant_id=_clean(os.getenv("MICROSOFT_APP_TENANT_ID")),
            raw_s3_bucket=_clean(os.getenv("RAW_S3_BUCKET")),
            report_s3_bucket=_clean(os.getenv("REPORT_S3_BUCKET")),
            athena_output_s3_uri=_clean(os.getenv("ATHENA_OUTPUT_S3_URI")),
            glue_database_name=_clean(os.getenv("GLUE_DATABASE_NAME")),
            aurora_database_url=_clean(os.getenv("AURORA_DATABASE_URL")),
            aurora_crm_schema=os.getenv("AURORA_CRM_SCHEMA", "public"),
            aurora_crm_accounts_table=os.getenv("AURORA_CRM_ACCOUNTS_TABLE", "accounts"),
            aurora_crm_company_name_column=os.getenv("AURORA_CRM_COMPANY_NAME_COLUMN", "company_name"),
            aurora_crm_licenses_table=os.getenv("AURORA_CRM_LICENSES_TABLE", "customer_licenses"),
            aurora_crm_license_id_column=os.getenv("AURORA_CRM_LICENSE_ID_COLUMN", "id"),
            aurora_crm_license_code_column=os.getenv("AURORA_CRM_LICENSE_CODE_COLUMN", "license_code"),
            aurora_crm_license_company_column=os.getenv("AURORA_CRM_LICENSE_COMPANY_COLUMN", "company"),
            aurora_crm_license_entity_column=os.getenv("AURORA_CRM_LICENSE_ENTITY_COLUMN", "entity"),
            aurora_crm_license_active_column=os.getenv("AURORA_CRM_LICENSE_ACTIVE_COLUMN", "active_license"),
            aurora_crm_license_expiry_column=os.getenv("AURORA_CRM_LICENSE_EXPIRY_COLUMN", "maintenance_expiry_date"),
            aurora_crm_linktek_entity_value=os.getenv("AURORA_CRM_LINKTEK_ENTITY_VALUE", "LinkTek"),
            aurora_crm_srf_table=os.getenv("AURORA_CRM_SRF_TABLE", "sales_routing_forms"),
            aurora_crm_srf_license_column=os.getenv("AURORA_CRM_SRF_LICENSE_COLUMN", "license_id"),
            aurora_crm_license_verifications_table=os.getenv(
                "AURORA_CRM_LICENSE_VERIFICATIONS_TABLE",
                "license_verifications",
            ),
            aurora_crm_license_verifications_license_column=os.getenv(
                "AURORA_CRM_LICENSE_VERIFICATIONS_LICENSE_COLUMN",
                "existing_license_record",
            ),
            aurora_crm_quote_line_items_table=os.getenv("AURORA_CRM_QUOTE_LINE_ITEMS_TABLE", "quote_line_item_sets"),
            aurora_crm_quote_line_items_license_column=os.getenv(
                "AURORA_CRM_QUOTE_LINE_ITEMS_LICENSE_COLUMN",
                "customer_license_record",
            ),
            dynamodb_source_tables=os.getenv("DYNAMODB_SOURCE_TABLES", "ProcessInfo,SiteInfo,TenantInfo"),
            dynamodb_source_system=os.getenv("DYNAMODB_SOURCE_SYSTEM", "aws_dynamodb"),
            dynamodb_scan_page_limit=_int_env("DYNAMODB_SCAN_PAGE_LIMIT", 1000),
            dynamodb_parallel_segments=_int_env("DYNAMODB_PARALLEL_SEGMENTS", 8),
            dynamodb_source_role_arn=_clean(os.getenv("DYNAMODB_SOURCE_ROLE_ARN")),
            dynamodb_source_external_id=_clean(os.getenv("DYNAMODB_SOURCE_EXTERNAL_ID")),
            solo_credentials_secret_name=_clean(os.getenv("SOLO_CREDENTIALS_SECRET_NAME")),
            solo_base_url=os.getenv("SOLO_BASE_URL", "https://secure.softwarekey.com/solo"),
            solo_author_id=_clean(os.getenv("SOLO_AUTHOR_ID")),
            solo_api_user_id=_clean(os.getenv("SOLO_API_USER_ID")),
            solo_api_user_password=_clean(os.getenv("SOLO_API_USER_PASSWORD")),
            zoho_credentials_secret_name=_clean(os.getenv("ZOHO_CREDENTIALS_SECRET_NAME")),
            zoho_datacenter=os.getenv("ZOHO_DATACENTER", "com"),
            zoho_client_id=_clean(os.getenv("ZOHO_CLIENT_ID")),
            zoho_client_secret=_clean(os.getenv("ZOHO_CLIENT_SECRET")),
            zoho_refresh_token=_clean(os.getenv("ZOHO_REFRESH_TOKEN")),
            zoho_grant_token=_clean(os.getenv("ZOHO_GRANT_TOKEN")),
            zoho_redirect_uri=_clean(os.getenv("ZOHO_REDIRECT_URI")),
            zoho_scopes=os.getenv("ZOHO_SCOPES", "ZohoCRM.modules.ALL"),
            zoho_crm_api_base=_clean(os.getenv("ZOHO_CRM_API_BASE")),
            zoho_analytics_workspace_name=os.getenv("ZOHO_ANALYTICS_WORKSPACE_NAME", "Statistics Pilot"),
            zoho_analytics_workspace_id=_clean(os.getenv("ZOHO_ANALYTICS_WORKSPACE_ID")),
            zoho_analytics_org_id=_clean(os.getenv("ZOHO_ANALYTICS_ORG_ID")),
        )
        return settings.with_secret_fallback() if enable_secret_fallback else settings

    def with_secret_fallback(self) -> "LicenseAgentSettings":
        solo_secret_payload: dict[str, object] = {}
        zoho_secret_payload: dict[str, object] = {}

        if self.solo_credentials_secret_name and not (
            self.solo_author_id and self.solo_api_user_id and self.solo_api_user_password
        ):
            solo_secret_payload = fetch_secret_json(
                self.solo_credentials_secret_name,
                region=self.aws_region,
                profile=self.aws_profile,
                aws_cli_path=self.aws_cli_path,
            )

        if not self.zoho_credentials_secret_name:
            if not solo_secret_payload:
                return self
        if self.zoho_credentials_secret_name and not (
            self.zoho_client_id and self.zoho_client_secret and (self.zoho_refresh_token or self.zoho_grant_token)
        ):
            zoho_secret_payload = fetch_secret_json(
                self.zoho_credentials_secret_name,
                region=self.aws_region,
                profile=self.aws_profile,
                aws_cli_path=self.aws_cli_path,
            )
        elif not solo_secret_payload:
            return self

        return LicenseAgentSettings(
            aws_region=self.aws_region,
            aws_profile=self.aws_profile,
            aws_cli_path=self.aws_cli_path,
            ingest_raw_root=self.ingest_raw_root,
            app_db_path=self.app_db_path,
            report_output_root=self.report_output_root,
            chat_store_backend=self.chat_store_backend,
            chat_state_table_name=self.chat_state_table_name,
            teams_shared_secret=self.teams_shared_secret,
            teams_app_secret_name=self.teams_app_secret_name,
            microsoft_app_id=self.microsoft_app_id,
            microsoft_app_password=self.microsoft_app_password,
            microsoft_app_type=self.microsoft_app_type,
            microsoft_app_tenant_id=self.microsoft_app_tenant_id,
            raw_s3_bucket=self.raw_s3_bucket,
            report_s3_bucket=self.report_s3_bucket,
            athena_output_s3_uri=self.athena_output_s3_uri,
            glue_database_name=self.glue_database_name,
            aurora_database_url=self.aurora_database_url,
            aurora_crm_schema=self.aurora_crm_schema,
            aurora_crm_accounts_table=self.aurora_crm_accounts_table,
            aurora_crm_company_name_column=self.aurora_crm_company_name_column,
            aurora_crm_licenses_table=self.aurora_crm_licenses_table,
            aurora_crm_license_id_column=self.aurora_crm_license_id_column,
            aurora_crm_license_code_column=self.aurora_crm_license_code_column,
            aurora_crm_license_company_column=self.aurora_crm_license_company_column,
            aurora_crm_license_entity_column=self.aurora_crm_license_entity_column,
            aurora_crm_license_active_column=self.aurora_crm_license_active_column,
            aurora_crm_license_expiry_column=self.aurora_crm_license_expiry_column,
            aurora_crm_linktek_entity_value=self.aurora_crm_linktek_entity_value,
            aurora_crm_srf_table=self.aurora_crm_srf_table,
            aurora_crm_srf_license_column=self.aurora_crm_srf_license_column,
            aurora_crm_license_verifications_table=self.aurora_crm_license_verifications_table,
            aurora_crm_license_verifications_license_column=self.aurora_crm_license_verifications_license_column,
            aurora_crm_quote_line_items_table=self.aurora_crm_quote_line_items_table,
            aurora_crm_quote_line_items_license_column=self.aurora_crm_quote_line_items_license_column,
            dynamodb_source_tables=self.dynamodb_source_tables,
            dynamodb_source_system=self.dynamodb_source_system,
            dynamodb_scan_page_limit=self.dynamodb_scan_page_limit,
            dynamodb_parallel_segments=self.dynamodb_parallel_segments,
            dynamodb_source_role_arn=self.dynamodb_source_role_arn,
            dynamodb_source_external_id=self.dynamodb_source_external_id,
            solo_credentials_secret_name=self.solo_credentials_secret_name,
            solo_base_url=_prefer(self.solo_base_url, solo_secret_payload.get("SOLO_BASE_URL"), "https://secure.softwarekey.com/solo"),
            solo_author_id=_prefer(self.solo_author_id, solo_secret_payload.get("SOLO_AUTHOR_ID")),
            solo_api_user_id=_prefer(self.solo_api_user_id, solo_secret_payload.get("SOLO_API_USER_ID")),
            solo_api_user_password=_prefer(
                self.solo_api_user_password,
                solo_secret_payload.get("SOLO_API_USER_PASSWORD"),
            ),
            zoho_credentials_secret_name=self.zoho_credentials_secret_name,
            zoho_datacenter=_prefer(self.zoho_datacenter, zoho_secret_payload.get("ZOHO_DATACENTER"), "com"),
            zoho_client_id=_prefer(self.zoho_client_id, zoho_secret_payload.get("ZOHO_CLIENT_ID")),
            zoho_client_secret=_prefer(self.zoho_client_secret, zoho_secret_payload.get("ZOHO_CLIENT_SECRET")),
            zoho_refresh_token=_prefer(self.zoho_refresh_token, zoho_secret_payload.get("ZOHO_REFRESH_TOKEN")),
            zoho_grant_token=_prefer(self.zoho_grant_token, zoho_secret_payload.get("ZOHO_GRANT_TOKEN")),
            zoho_redirect_uri=_prefer(self.zoho_redirect_uri, zoho_secret_payload.get("ZOHO_REDIRECT_URI")),
            zoho_scopes=_prefer(self.zoho_scopes, zoho_secret_payload.get("ZOHO_SCOPES"), "ZohoCRM.modules.ALL"),
            zoho_crm_api_base=_prefer(self.zoho_crm_api_base, zoho_secret_payload.get("ZOHO_CRM_API_BASE")),
            zoho_analytics_workspace_name=_prefer(
                self.zoho_analytics_workspace_name,
                zoho_secret_payload.get("ZOHO_ANALYTICS_WORKSPACE_NAME"),
                "Statistics Pilot",
            ),
            zoho_analytics_workspace_id=_prefer(
                self.zoho_analytics_workspace_id,
                zoho_secret_payload.get("ZOHO_ANALYTICS_WORKSPACE_ID"),
            ),
            zoho_analytics_org_id=_prefer(
                self.zoho_analytics_org_id,
                zoho_secret_payload.get("ZOHO_ANALYTICS_ORG_ID"),
            ),
        )

    def aws_cli_status(self) -> dict[str, object]:
        info = get_aws_cli_info(self.aws_cli_path)
        return {
            "available": info.available,
            "path": info.path,
            "version": info.version,
            "profile": self.aws_profile,
            "region": self.aws_region,
        }

    def ingest_status(self) -> dict[str, object]:
        return {
            "raw_root": self.ingest_raw_root,
            "app_db_path": self.app_db_path,
            "report_output_root": self.report_output_root,
            "chat_store_backend": self.chat_store_backend,
            "chat_state_table_name": self.chat_state_table_name,
            "teams_app_secret_name": self.teams_app_secret_name,
            "microsoft_app_id_configured": bool(self.microsoft_app_id or self.teams_app_secret_name),
            "microsoft_app_type": self.microsoft_app_type,
            "microsoft_app_tenant_id_configured": bool(self.microsoft_app_tenant_id or self.teams_app_secret_name),
            "raw_s3_bucket": self.raw_s3_bucket,
            "report_s3_bucket": self.report_s3_bucket,
            "athena_output_s3_uri": self.athena_output_s3_uri,
            "glue_database_name": self.glue_database_name,
            "dynamodb_source_tables": self.dynamodb_source_tables,
            "dynamodb_source_system": self.dynamodb_source_system,
            "dynamodb_scan_page_limit": self.dynamodb_scan_page_limit,
            "dynamodb_parallel_segments": self.dynamodb_parallel_segments,
            "dynamodb_source_role_arn_configured": bool(self.dynamodb_source_role_arn),
            "aurora_database_url_configured": bool(self.aurora_database_url),
            "aurora_crm_schema": self.aurora_crm_schema,
            "aurora_crm_accounts_table": self.aurora_crm_accounts_table,
            "aurora_crm_company_name_column": self.aurora_crm_company_name_column,
            "aurora_crm_licenses_table": self.aurora_crm_licenses_table,
            "aurora_crm_license_company_column": self.aurora_crm_license_company_column,
            "aurora_crm_license_active_column": self.aurora_crm_license_active_column,
            "aurora_crm_license_expiry_column": self.aurora_crm_license_expiry_column,
            "aurora_crm_linktek_entity_value": self.aurora_crm_linktek_entity_value,
        }

    def zoho_status(self) -> dict[str, object]:
        return {
            "credentials_source": "aws-secrets-manager" if self.zoho_credentials_secret_name else "environment",
            "secret_name": self.zoho_credentials_secret_name,
            "datacenter": self.zoho_datacenter,
            "has_client_id": bool(self.zoho_client_id),
            "has_client_secret": bool(self.zoho_client_secret),
            "has_refresh_token": bool(self.zoho_refresh_token),
            "has_grant_token": bool(self.zoho_grant_token),
            "has_redirect_uri": bool(self.zoho_redirect_uri),
            "analytics_workspace_name": self.zoho_analytics_workspace_name,
            "analytics_workspace_id": self.zoho_analytics_workspace_id,
            "analytics_org_id": self.zoho_analytics_org_id,
        }

    def solo_status(self) -> dict[str, object]:
        return {
            "credentials_source": "aws-secrets-manager" if self.solo_credentials_secret_name else "environment",
            "secret_name": self.solo_credentials_secret_name,
            "base_url": self.solo_base_url,
            "has_author_id": bool(self.solo_author_id),
            "has_api_user_id": bool(self.solo_api_user_id),
            "has_api_user_password": bool(self.solo_api_user_password),
        }


def safe_load_settings(dotenv_path: str | Path | None = None) -> tuple[LicenseAgentSettings, str | None]:
    try:
        return LicenseAgentSettings.from_env(dotenv_path=dotenv_path), None
    except AwsCliError as exc:
        return LicenseAgentSettings.from_env(dotenv_path=dotenv_path, enable_secret_fallback=False), str(exc)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _prefer(current: str | None, secret_value: object, default: str | None = None) -> str | None:
    if current:
        return current
    if isinstance(secret_value, str) and secret_value.strip():
        return secret_value.strip()
    return default


def _int_env(key: str, default: int) -> int:
    value = os.getenv(key)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default
