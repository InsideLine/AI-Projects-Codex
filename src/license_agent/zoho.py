from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .settings import LicenseAgentSettings


class ZohoError(RuntimeError):
    pass


@dataclass(frozen=True)
class ZohoConnectionStatus:
    can_refresh_access_token: bool
    can_build_authorization_url: bool
    workspace_name: str
    workspace_id: str | None
    org_id: str | None


class ZohoClient:
    _accounts_domains = {
        "com": "accounts.zoho.com",
        "eu": "accounts.zoho.eu",
        "in": "accounts.zoho.in",
        "com.au": "accounts.zoho.com.au",
        "jp": "accounts.zoho.jp",
        "ca": "accounts.zohocloud.ca",
        "sa": "accounts.zoho.sa",
    }
    _crm_api_domains = {
        "com": "www.zohoapis.com",
        "eu": "www.zohoapis.eu",
        "in": "www.zohoapis.in",
        "com.au": "www.zohoapis.com.au",
        "jp": "www.zohoapis.jp",
        "ca": "www.zohocloud.ca",
        "sa": "www.zohoapis.sa",
    }

    def __init__(self, settings: LicenseAgentSettings) -> None:
        datacenter = (settings.zoho_datacenter or "com").strip().lower()
        self.datacenter = datacenter if datacenter in self._accounts_domains else "com"
        self.settings = settings
        self.accounts_base = f"https://{self._accounts_domains[self.datacenter]}"
        self.crm_api_base = settings.zoho_crm_api_base or f"https://{self._crm_api_domains[self.datacenter]}"

    def status(self) -> ZohoConnectionStatus:
        return ZohoConnectionStatus(
            can_refresh_access_token=bool(
                self.settings.zoho_client_id and self.settings.zoho_client_secret and self.settings.zoho_refresh_token
            ),
            can_build_authorization_url=bool(
                self.settings.zoho_client_id and self.settings.zoho_redirect_uri and self.settings.zoho_scopes
            ),
            workspace_name=self.settings.zoho_analytics_workspace_name,
            workspace_id=self.settings.zoho_analytics_workspace_id,
            org_id=self.settings.zoho_analytics_org_id,
        )

    def build_authorization_url(self, state: str | None = None) -> str:
        if not self.settings.zoho_client_id:
            raise ZohoError("ZOHO_CLIENT_ID is required.")
        if not self.settings.zoho_redirect_uri:
            raise ZohoError("ZOHO_REDIRECT_URI is required.")
        if not self.settings.zoho_scopes:
            raise ZohoError("ZOHO_SCOPES is required.")

        params = {
            "response_type": "code",
            "client_id": self.settings.zoho_client_id,
            "scope": self.settings.zoho_scopes,
            "redirect_uri": self.settings.zoho_redirect_uri,
            "access_type": "offline",
            "prompt": "consent",
        }
        if state:
            params["state"] = state
        return f"{self.accounts_base}/oauth/v2/auth?{urlencode(params)}"

    def refresh_access_token(self) -> dict[str, Any]:
        if not self.settings.zoho_refresh_token:
            raise ZohoError("ZOHO_REFRESH_TOKEN is required to refresh access.")
        payload = {
            "grant_type": "refresh_token",
            "client_id": self._require(self.settings.zoho_client_id, "ZOHO_CLIENT_ID"),
            "client_secret": self._require(self.settings.zoho_client_secret, "ZOHO_CLIENT_SECRET"),
            "refresh_token": self.settings.zoho_refresh_token,
        }
        return self._token_request(payload)

    def exchange_grant_token(self, grant_token: str | None = None) -> dict[str, Any]:
        payload = {
            "grant_type": "authorization_code",
            "client_id": self._require(self.settings.zoho_client_id, "ZOHO_CLIENT_ID"),
            "client_secret": self._require(self.settings.zoho_client_secret, "ZOHO_CLIENT_SECRET"),
            "redirect_uri": self._require(self.settings.zoho_redirect_uri, "ZOHO_REDIRECT_URI"),
            "code": self._require(grant_token or self.settings.zoho_grant_token, "ZOHO_GRANT_TOKEN"),
        }
        return self._token_request(payload)

    def search_records(self, module: str, criteria: str, access_token: str) -> list[dict[str, Any]]:
        if not module or not criteria:
            raise ZohoError("Both module and criteria are required.")
        if not access_token:
            raise ZohoError("Access token is required.")

        url = f"{self.crm_api_base.rstrip('/')}/crm/v6/{module}/search?{urlencode({'criteria': criteria})}"
        response = self._request_json(
            url,
            headers={"Authorization": f"Zoho-oauthtoken {access_token}"},
        )
        data = response.get("data")
        if not isinstance(data, list):
            return []
        return [row for row in data if isinstance(row, dict)]

    def _token_request(self, payload: dict[str, str]) -> dict[str, Any]:
        return self._request_json(
            f"{self.accounts_base}/oauth/v2/token",
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=urlencode(payload).encode("utf-8"),
        )

    def _request_json(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
    ) -> dict[str, Any]:
        request = Request(url, headers=headers or {}, data=data, method=method)
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover
            raise ZohoError(f"Zoho request failed: {exc}") from exc

        if isinstance(payload, dict) and payload.get("error"):
            raise ZohoError(f"Zoho returned an error payload: {payload}")
        return payload

    def _require(self, value: str | None, name: str) -> str:
        if not value:
            raise ZohoError(f"{name} is required.")
        return value

