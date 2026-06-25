from unittest import TestCase

from license_agent.bot_framework import (
    BotFrameworkActivityHandler,
    BotFrameworkCredentials,
    activity_message_text,
    activity_user_id,
    build_reply_activity,
    is_bot_framework_activity,
)
from license_agent.settings import LicenseAgentSettings
from license_agent.teams_service import TeamsChatService


class AllowAllAuthenticator:
    def validate_activity_token(self, **kwargs):
        return {"aud": kwargs["app_id"]}


class CapturingReplyClient:
    def __init__(self) -> None:
        self.replies = []

    def reply_to_activity(self, activity, text):
        self.replies.append((activity, text))
        return {"status_code": 200, "body": {"id": "reply-1"}}


class BotFrameworkTests(TestCase):
    def test_detects_bot_framework_activity(self) -> None:
        self.assertTrue(
            is_bot_framework_activity(
                {
                    "type": "message",
                    "serviceUrl": "https://smba.trafficmanager.net/teams/",
                    "conversation": {"id": "conversation-id"},
                }
            )
        )
        self.assertFalse(is_bot_framework_activity({"text": "history", "user_email": "user@example.com"}))

    def test_cleans_teams_mention_from_message_text(self) -> None:
        self.assertEqual(activity_message_text({"text": "<at>License Analyzer</at> history"}), "history")

    def test_uses_channel_user_identity(self) -> None:
        self.assertEqual(
            activity_user_id(
                {
                    "from": {"id": "fallback"},
                    "channelData": {"user": {"userPrincipalName": "analyst@example.com"}},
                }
            ),
            "analyst@example.com",
        )

    def test_builds_reply_activity(self) -> None:
        reply = build_reply_activity(
            {
                "id": "activity-id",
                "recipient": {"id": "bot-id"},
                "from": {"id": "user-id"},
                "conversation": {"id": "conversation-id"},
            },
            "Hello",
        )
        self.assertEqual(reply["type"], "message")
        self.assertEqual(reply["from"]["id"], "bot-id")
        self.assertEqual(reply["recipient"]["id"], "user-id")
        self.assertEqual(reply["replyToId"], "activity-id")
        self.assertEqual(reply["text"], "Hello")

    def test_handler_routes_message_to_teams_service_and_replies(self) -> None:
        reply_client = CapturingReplyClient()
        handler = BotFrameworkActivityHandler(
            credentials=BotFrameworkCredentials(app_id="app-id", app_password="app-password"),
            reply_client=reply_client,
            authenticator=AllowAllAuthenticator(),
        )
        service = TeamsChatService(LicenseAgentSettings(app_db_path=":memory:"), run_async=False)
        response = handler.handle(
            activity={
                "type": "message",
                "id": "activity-id",
                "serviceUrl": "https://smba.trafficmanager.net/teams/",
                "from": {"id": "user-id"},
                "recipient": {"id": "bot-id"},
                "conversation": {"id": "conversation-id"},
                "text": "<at>License Analyzer</at> history",
            },
            authorization_header="Bearer token",
            teams_service=service,
        )
        self.assertEqual(response["type"], "bot_framework_message")
        self.assertEqual(reply_client.replies[0][1], "No report requests have been queued yet for this user.")
