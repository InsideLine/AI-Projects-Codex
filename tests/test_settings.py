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
            self.assertEqual(settings.zoho_client_id, "client-id")
            self.assertEqual(settings.zoho_refresh_token, "refresh-token")

    def test_uses_secret_manager_values_when_requested(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AWS_REGION": "us-east-1",
                "ZOHO_CREDENTIALS_SECRET_NAME": "AxiomProjects/ZohoCRM",
            },
            clear=True,
        ):
            with patch(
                "license_agent.settings.fetch_secret_json",
                return_value={
                    "ZOHO_CLIENT_ID": "client-id",
                    "ZOHO_CLIENT_SECRET": "client-secret",
                    "ZOHO_REFRESH_TOKEN": "refresh-token",
                    "ZOHO_ANALYTICS_WORKSPACE_ID": "workspace-id",
                },
            ):
                settings = LicenseAgentSettings.from_env()

        self.assertEqual(settings.zoho_client_id, "client-id")
        self.assertEqual(settings.zoho_client_secret, "client-secret")
        self.assertEqual(settings.zoho_refresh_token, "refresh-token")
        self.assertEqual(settings.zoho_analytics_workspace_id, "workspace-id")
