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
        if not self.zoho_credentials_secret_name:
            return self
        if self.zoho_client_id and self.zoho_client_secret and (self.zoho_refresh_token or self.zoho_grant_token):
            return self

        secret_payload = fetch_secret_json(
            self.zoho_credentials_secret_name,
            region=self.aws_region,
            profile=self.aws_profile,
            aws_cli_path=self.aws_cli_path,
        )
        return LicenseAgentSettings(
            aws_region=self.aws_region,
            aws_profile=self.aws_profile,
            aws_cli_path=self.aws_cli_path,
            zoho_credentials_secret_name=self.zoho_credentials_secret_name,
            zoho_datacenter=_prefer(self.zoho_datacenter, secret_payload.get("ZOHO_DATACENTER"), "com"),
            zoho_client_id=_prefer(self.zoho_client_id, secret_payload.get("ZOHO_CLIENT_ID")),
            zoho_client_secret=_prefer(self.zoho_client_secret, secret_payload.get("ZOHO_CLIENT_SECRET")),
            zoho_refresh_token=_prefer(self.zoho_refresh_token, secret_payload.get("ZOHO_REFRESH_TOKEN")),
            zoho_grant_token=_prefer(self.zoho_grant_token, secret_payload.get("ZOHO_GRANT_TOKEN")),
            zoho_redirect_uri=_prefer(self.zoho_redirect_uri, secret_payload.get("ZOHO_REDIRECT_URI")),
            zoho_scopes=_prefer(self.zoho_scopes, secret_payload.get("ZOHO_SCOPES"), "ZohoCRM.modules.ALL"),
            zoho_crm_api_base=_prefer(self.zoho_crm_api_base, secret_payload.get("ZOHO_CRM_API_BASE")),
            zoho_analytics_workspace_name=_prefer(
                self.zoho_analytics_workspace_name,
                secret_payload.get("ZOHO_ANALYTICS_WORKSPACE_NAME"),
                "Statistics Pilot",
            ),
            zoho_analytics_workspace_id=_prefer(
                self.zoho_analytics_workspace_id,
                secret_payload.get("ZOHO_ANALYTICS_WORKSPACE_ID"),
            ),
            zoho_analytics_org_id=_prefer(
                self.zoho_analytics_org_id,
                secret_payload.get("ZOHO_ANALYTICS_ORG_ID"),
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
