from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

from .settings import LicenseAgentSettings
from .teams_service import TeamsChatService


BOT_CONNECTOR_OPENID_KEYS_URL = "https://login.botframework.com/v1/.well-known/keys"
BOT_CONNECTOR_ISSUER = "https://api.botframework.com"
BOT_CONNECTOR_SCOPE = "https://api.botframework.com/.default"


@dataclass(frozen=True)
class BotFrameworkCredentials:
    app_id: str
    app_password: str
    app_type: str = "SingleTenant"
    tenant_id: str | None = None

    @property
    def token_url(self) -> str:
        tenant = self.tenant_id if self.app_type.lower() == "singletenant" and self.tenant_id else "botframework.com"
        return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"


class ActivityReplyClient(Protocol):
    def reply_to_activity(self, activity: dict[str, Any], text: str) -> dict[str, Any]:
        ...


class BotFrameworkAuthError(RuntimeError):
    pass


class BotFrameworkReplyError(RuntimeError):
    pass


class BotFrameworkAuthenticator:
    def __init__(self, *, jwks_url: str = BOT_CONNECTOR_OPENID_KEYS_URL) -> None:
        self.jwks_url = jwks_url

    def validate_activity_token(
        self,
        *,
        authorization_header: str | None,
        activity: dict[str, Any],
        app_id: str,
    ) -> dict[str, Any]:
        token = _bearer_token(authorization_header)
        if not token:
            raise BotFrameworkAuthError("Missing Bot Framework bearer token.")

        try:
            import jwt
        except ImportError as exc:  # pragma: no cover
            raise BotFrameworkAuthError("PyJWT is required for Bot Framework token validation.") from exc

        try:
            signing_key = jwt.PyJWKClient(self.jwks_url).get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=app_id,
                issuer=BOT_CONNECTOR_ISSUER,
                leeway=300,
            )
        except Exception as exc:
            raise BotFrameworkAuthError("Invalid Bot Framework bearer token.") from exc

        token_service_url = claims.get("serviceurl") or claims.get("serviceUrl")
        activity_service_url = activity.get("serviceUrl")
        if token_service_url and activity_service_url and token_service_url != activity_service_url:
            raise BotFrameworkAuthError("Bot Framework token serviceUrl does not match activity serviceUrl.")
        return dict(claims)


class BotFrameworkConnectorClient:
    def __init__(self, credentials: BotFrameworkCredentials) -> None:
        self.credentials = credentials
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0

    def reply_to_activity(self, activity: dict[str, Any], text: str) -> dict[str, Any]:
        service_url = str(activity.get("serviceUrl") or "").rstrip("/")
        conversation_id = str((activity.get("conversation") or {}).get("id") or "")
        activity_id = str(activity.get("id") or "")
        if not service_url or not conversation_id or not activity_id:
            raise BotFrameworkReplyError("Activity is missing serviceUrl, conversation.id, or id.")

        url = (
            f"{service_url}/v3/conversations/{urllib.parse.quote(conversation_id, safe='')}"
            f"/activities/{urllib.parse.quote(activity_id, safe='')}"
        )
        payload = build_reply_activity(activity, text)
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.get_access_token()}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                body = response.read().decode("utf-8")
                return {
                    "status_code": response.status,
                    "body": json.loads(body) if body.strip() else {},
                }
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise BotFrameworkReplyError(f"Bot Connector reply failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise BotFrameworkReplyError(f"Bot Connector reply failed: {exc}") from exc

    def get_access_token(self) -> str:
        now = time.time()
        if self._access_token and now < self._access_token_expires_at:
            return self._access_token

        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.credentials.app_id,
                "client_secret": self.credentials.app_password,
                "scope": BOT_CONNECTOR_SCOPE,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.credentials.token_url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise BotFrameworkReplyError(f"Bot Connector token request failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise BotFrameworkReplyError(f"Bot Connector token request failed: {exc}") from exc

        token = str(payload.get("access_token") or "")
        if not token:
            raise BotFrameworkReplyError("Bot Connector token response did not include access_token.")
        expires_in = int(payload.get("expires_in") or 3600)
        self._access_token = token
        self._access_token_expires_at = now + max(60, expires_in - 300)
        return token


class BotFrameworkActivityHandler:
    def __init__(
        self,
        *,
        credentials: BotFrameworkCredentials,
        reply_client: ActivityReplyClient | None = None,
        authenticator: BotFrameworkAuthenticator | None = None,
    ) -> None:
        self.credentials = credentials
        self.reply_client = reply_client or BotFrameworkConnectorClient(credentials)
        self.authenticator = authenticator or BotFrameworkAuthenticator()

    def handle(
        self,
        *,
        activity: dict[str, Any],
        authorization_header: str | None,
        teams_service: TeamsChatService,
    ) -> dict[str, Any]:
        self.authenticator.validate_activity_token(
            authorization_header=authorization_header,
            activity=activity,
            app_id=self.credentials.app_id,
        )

        activity_type = str(activity.get("type") or "").lower()
        if activity_type == "message":
            text = activity_message_text(activity)
            if not text:
                reply_text = "Send me a license ID, company name, or license usage question and I will help investigate it."
            else:
                response = teams_service.handle_message(text, activity_user_id(activity))
                reply_text = str(response.get("message") or "I received that, but I do not have a text response yet.")
            reply_response = self.reply_client.reply_to_activity(activity, reply_text)
            return {"type": "bot_framework_message", "reply": reply_response}

        if activity_type == "conversationupdate":
            welcome_text = conversation_update_welcome_text(activity)
            if welcome_text:
                reply_response = self.reply_client.reply_to_activity(activity, welcome_text)
                return {"type": "bot_framework_conversation_update", "reply": reply_response}
            return {"type": "bot_framework_ignored", "activity_type": activity_type}

        return {"type": "bot_framework_ignored", "activity_type": activity_type}


def is_bot_framework_activity(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("type") and payload.get("serviceUrl") and payload.get("conversation"))


def activity_message_text(activity: dict[str, Any]) -> str:
    text = str(activity.get("text") or "")
    text = re.sub(r"<at>.*?</at>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def activity_user_id(activity: dict[str, Any]) -> str:
    sender = activity.get("from") or {}
    channel_data = activity.get("channelData") or {}
    user = channel_data.get("user") or {}
    for value in (
        user.get("userPrincipalName"),
        user.get("email"),
        sender.get("aadObjectId"),
        sender.get("id"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "teams-user"


def build_reply_activity(activity: dict[str, Any], text: str) -> dict[str, Any]:
    payload = {
        "type": "message",
        "from": activity.get("recipient") or {},
        "recipient": activity.get("from") or {},
        "conversation": activity.get("conversation") or {},
        "replyToId": activity.get("id"),
        "textFormat": "markdown",
        "text": text,
    }
    if activity.get("locale"):
        payload["locale"] = activity.get("locale")
    return payload


def conversation_update_welcome_text(activity: dict[str, Any]) -> str:
    members_added = activity.get("membersAdded") or []
    recipient_id = str((activity.get("recipient") or {}).get("id") or "")
    for member in members_added:
        member_id = str((member or {}).get("id") or "")
        if member_id and member_id != recipient_id:
            return "License Analyzer is ready. Ask me about a LinkTek license ID, company, or usage pattern."
    return ""


@lru_cache(maxsize=1)
def load_bot_framework_credentials(settings: LicenseAgentSettings) -> BotFrameworkCredentials:
    payload: dict[str, Any] = {}
    if settings.teams_app_secret_name:
        import boto3

        client = boto3.client("secretsmanager", region_name=settings.aws_region)
        response = client.get_secret_value(SecretId=settings.teams_app_secret_name)
        payload = json.loads(str(response.get("SecretString") or "{}"))

    app_id = _prefer(settings.microsoft_app_id, payload.get("MICROSOFT_APP_ID"))
    app_password = _prefer(settings.microsoft_app_password, payload.get("MICROSOFT_APP_PASSWORD"))
    app_type = _prefer(settings.microsoft_app_type, payload.get("MICROSOFT_APP_TYPE")) or "SingleTenant"
    tenant_id = _prefer(settings.microsoft_app_tenant_id, payload.get("MICROSOFT_APP_TENANT_ID"))
    if not app_id or not app_password:
        raise BotFrameworkAuthError("Microsoft Teams bot app ID/password are not configured.")
    return BotFrameworkCredentials(
        app_id=app_id,
        app_password=app_password,
        app_type=app_type,
        tenant_id=tenant_id,
    )


def _bearer_token(header: str | None) -> str:
    if not header:
        return ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return ""
    return value.strip()


def _prefer(current: str | None, secret_value: object) -> str | None:
    if current:
        return current
    if isinstance(secret_value, str) and secret_value.strip():
        return secret_value.strip()
    return None
