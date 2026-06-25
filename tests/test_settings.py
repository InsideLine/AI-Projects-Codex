import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from license_agent.settings import LicenseAgentSettings


class SettingsTests(TestCase):
    def test_loads_direct_env_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "AWS_REGION=us-east-1",
                        "AWS_CLI_PATH=/custom/aws",
                        "APP_DB_PATH=/tmp/license-agent.sqlite3",
                        "REPORT_OUTPUT_ROOT=/tmp/license-reports",
                        "CHAT_STORE_BACKEND=dynamodb",
                        "CHAT_STATE_TABLE_NAME=chat-state",
                        "TEAMS_SHARED_SECRET=test-secret",
                        "TEAMS_APP_SECRET_NAME=license-violation-agent/ms-teams-app",
                        "MICROSOFT_APP_ID=app-id",
                        "MICROSOFT_APP_PASSWORD=app-password",
                        "MICROSOFT_APP_TYPE=SingleTenant",
                        "MICROSOFT_APP_TENANT_ID=tenant-id",
                        "RAW_S3_BUCKET=raw-bucket",
                        "REPORT_S3_BUCKET=reports-bucket",
                        "AURORA_CRM_SCHEMA=crm",
                        "AURORA_CRM_ACCOUNTS_TABLE=crm_accounts",
                        "AURORA_CRM_COMPANY_NAME_COLUMN=account_name",
                        "AURORA_CRM_LICENSES_TABLE=licenses",
                        "AURORA_CRM_LICENSE_ID_COLUMN=crm_id",
                        "AURORA_CRM_LICENSE_CODE_COLUMN=serial",
                        "AURORA_CRM_LICENSE_COMPANY_COLUMN=account",
                        "AURORA_CRM_LICENSE_ENTITY_COLUMN=business_unit",
                        "AURORA_CRM_LICENSE_ACTIVE_COLUMN=is_active",
                        "AURORA_CRM_LICENSE_EXPIRY_COLUMN=expires_at",
                        "AURORA_CRM_LINKTEK_ENTITY_VALUE=LinkTek Test",
                        "AURORA_CRM_SRF_TABLE=srfs",
                        "AURORA_CRM_SRF_LICENSE_COLUMN=srf_license",
                        "AURORA_CRM_LICENSE_VERIFICATIONS_TABLE=verification_cases",
                        "AURORA_CRM_LICENSE_VERIFICATIONS_LICENSE_COLUMN=verified_license",
                        "AURORA_CRM_QUOTE_LINE_ITEMS_TABLE=line_items",
                        "AURORA_CRM_QUOTE_LINE_ITEMS_LICENSE_COLUMN=line_license",
                        "DYNAMODB_SOURCE_TABLES=ProcessInfo,SiteInfo",
                        "DYNAMODB_SOURCE_SYSTEM=aws_dynamodb_weekly",
                        "DYNAMODB_SCAN_PAGE_LIMIT=500",
                        "DYNAMODB_PARALLEL_SEGMENTS=4",
                        "DYNAMODB_SOURCE_ROLE_ARN=arn:aws:iam::123456789012:role/source-role",
                        "DYNAMODB_SOURCE_EXTERNAL_ID=external-id",
                        "ZOHO_CLIENT_ID=client-id",
                        "ZOHO_CLIENT_SECRET=client-secret",
                        "ZOHO_REFRESH_TOKEN=refresh-token",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                settings = LicenseAgentSettings.from_env(env_path)

            self.assertEqual(settings.aws_cli_path, "/custom/aws")
            self.assertEqual(settings.app_db_path, "/tmp/license-agent.sqlite3")
            self.assertEqual(settings.report_output_root, "/tmp/license-reports")
            self.assertEqual(settings.chat_store_backend, "dynamodb")
            self.assertEqual(settings.chat_state_table_name, "chat-state")
            self.assertEqual(settings.teams_shared_secret, "test-secret")
            self.assertEqual(settings.teams_app_secret_name, "license-violation-agent/ms-teams-app")
            self.assertEqual(settings.microsoft_app_id, "app-id")
            self.assertEqual(settings.microsoft_app_password, "app-password")
            self.assertEqual(settings.microsoft_app_type, "SingleTenant")
            self.assertEqual(settings.microsoft_app_tenant_id, "tenant-id")
            self.assertEqual(settings.raw_s3_bucket, "raw-bucket")
            self.assertEqual(settings.report_s3_bucket, "reports-bucket")
            self.assertEqual(settings.aurora_crm_schema, "crm")
            self.assertEqual(settings.aurora_crm_accounts_table, "crm_accounts")
            self.assertEqual(settings.aurora_crm_company_name_column, "account_name")
            self.assertEqual(settings.aurora_crm_licenses_table, "licenses")
            self.assertEqual(settings.aurora_crm_license_id_column, "crm_id")
            self.assertEqual(settings.aurora_crm_license_code_column, "serial")
            self.assertEqual(settings.aurora_crm_license_company_column, "account")
            self.assertEqual(settings.aurora_crm_license_entity_column, "business_unit")
            self.assertEqual(settings.aurora_crm_license_active_column, "is_active")
            self.assertEqual(settings.aurora_crm_license_expiry_column, "expires_at")
            self.assertEqual(settings.aurora_crm_linktek_entity_value, "LinkTek Test")
            self.assertEqual(settings.aurora_crm_srf_table, "srfs")
            self.assertEqual(settings.aurora_crm_srf_license_column, "srf_license")
            self.assertEqual(settings.aurora_crm_license_verifications_table, "verification_cases")
            self.assertEqual(settings.aurora_crm_license_verifications_license_column, "verified_license")
            self.assertEqual(settings.aurora_crm_quote_line_items_table, "line_items")
            self.assertEqual(settings.aurora_crm_quote_line_items_license_column, "line_license")
            self.assertEqual(settings.dynamodb_source_tables, "ProcessInfo,SiteInfo")
            self.assertEqual(settings.dynamodb_source_system, "aws_dynamodb_weekly")
            self.assertEqual(settings.dynamodb_scan_page_limit, 500)
            self.assertEqual(settings.dynamodb_parallel_segments, 4)
            self.assertEqual(settings.dynamodb_source_role_arn, "arn:aws:iam::123456789012:role/source-role")
            self.assertEqual(settings.dynamodb_source_external_id, "external-id")
            self.assertEqual(settings.zoho_client_id, "client-id")
            self.assertEqual(settings.zoho_refresh_token, "refresh-token")

    def test_uses_secret_manager_values_when_requested(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AWS_REGION": "us-east-1",
                "SOLO_CREDENTIALS_SECRET_NAME": "AxiomProjects/SOLO",
                "ZOHO_CREDENTIALS_SECRET_NAME": "AxiomProjects/ZohoCRM",
            },
            clear=True,
        ):
            with patch(
                "license_agent.settings.fetch_secret_json",
                side_effect=[
                    {
                        "SOLO_AUTHOR_ID": "author-id",
                        "SOLO_API_USER_ID": "solo-user",
                        "SOLO_API_USER_PASSWORD": "solo-pass",
                    },
                    {
                        "ZOHO_CLIENT_ID": "client-id",
                        "ZOHO_CLIENT_SECRET": "client-secret",
                        "ZOHO_REFRESH_TOKEN": "refresh-token",
                        "ZOHO_ANALYTICS_WORKSPACE_ID": "workspace-id",
                    },
                ],
            ):
                settings = LicenseAgentSettings.from_env()

        self.assertEqual(settings.solo_author_id, "author-id")
        self.assertEqual(settings.solo_api_user_id, "solo-user")
        self.assertEqual(settings.zoho_client_id, "client-id")
        self.assertEqual(settings.zoho_client_secret, "client-secret")
        self.assertEqual(settings.zoho_refresh_token, "refresh-token")
        self.assertEqual(settings.zoho_analytics_workspace_id, "workspace-id")
