from __future__ import annotations

import json
from pathlib import Path

from license_agent.settings import safe_load_settings
from license_agent.zoho import ZohoClient


def main() -> None:
    dotenv_path = Path(".env")
    settings, warning = safe_load_settings(dotenv_path if dotenv_path.exists() else None)
    zoho = ZohoClient(settings)

    payload = {
        "aws": settings.aws_cli_status(),
        "zoho": {
            **settings.zoho_status(),
            **zoho.status().__dict__,
        },
        "warning": warning,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

