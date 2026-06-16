from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


DEFAULT_AWS_CLI_CANDIDATES = (
    "/Users/joeyrogers/Library/Python/3.9/bin/aws",
    "/opt/homebrew/bin/aws",
    "/usr/local/bin/aws",
)


class AwsCliError(RuntimeError):
    pass


@dataclass(frozen=True)
class AwsCliInfo:
    path: str | None
    version: str | None

    @property
    def available(self) -> bool:
        return bool(self.path and self.version)


def resolve_aws_cli(explicit_path: str | None = None) -> str | None:
    if explicit_path:
        candidate = Path(explicit_path).expanduser()
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)

    discovered = shutil.which("aws")
    if discovered:
        return discovered

    for candidate in DEFAULT_AWS_CLI_CANDIDATES:
        if Path(candidate).exists() and os.access(candidate, os.X_OK):
            return candidate
    return None


def get_aws_cli_info(explicit_path: str | None = None) -> AwsCliInfo:
    cli_path = resolve_aws_cli(explicit_path)
    if not cli_path:
        return AwsCliInfo(path=None, version=None)

    try:
        completed = subprocess.run(
            [cli_path, "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return AwsCliInfo(path=cli_path, version=None)

    version_text = completed.stdout.strip() or completed.stderr.strip() or None
    return AwsCliInfo(path=cli_path, version=version_text)


def fetch_secret_json(
    secret_id: str,
    *,
    region: str,
    profile: str | None = None,
    aws_cli_path: str | None = None,
) -> dict[str, object]:
    cli_path = resolve_aws_cli(aws_cli_path)
    if not cli_path:
        raise AwsCliError("AWS CLI was not found on PATH or in known install locations.")

    command = [
        cli_path,
        "secretsmanager",
        "get-secret-value",
        "--secret-id",
        secret_id,
        "--region",
        region,
        "--output",
        "json",
    ]
    if profile:
        command.extend(["--profile", profile])

    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise AwsCliError(f"Failed to read secret '{secret_id}' via AWS CLI: {stderr or exc}") from exc
    except OSError as exc:
        raise AwsCliError(f"Failed to execute AWS CLI: {exc}") from exc

    payload = json.loads(completed.stdout)
    secret_string = payload.get("SecretString")
    if not isinstance(secret_string, str) or not secret_string.strip():
        raise AwsCliError(f"Secret '{secret_id}' did not contain a JSON SecretString payload.")

    try:
        decoded = json.loads(secret_string)
    except json.JSONDecodeError as exc:
        raise AwsCliError(f"Secret '{secret_id}' SecretString is not valid JSON.") from exc

    if not isinstance(decoded, dict):
        raise AwsCliError(f"Secret '{secret_id}' JSON payload must be an object.")
    return decoded

