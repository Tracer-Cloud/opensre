"""LangSmith deployment helpers for the OpenSRE CLI."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import httpx

from app.cli.wizard.env_sync import sync_env_values
from app.integrations.store import get_integration


def _run_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a command and return the completed process."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", f"Command not found: {cmd[0]}")


def is_docker_running() -> tuple[bool, str]:
    """Check whether Docker is installed and the daemon is running."""
    if not shutil.which("docker"):
        return False, "Docker is not installed."

    result = _run_command(["docker", "info"])
    if result.returncode != 0:
        return False, "Docker is not running."

    return True, "Docker is running."


def is_langgraph_cli_installed() -> tuple[bool, str]:
    """Check whether the langgraph CLI is installed and callable."""
    if not shutil.which("langgraph"):
        return False, "langgraph CLI is not installed."

    result = _run_command(["langgraph", "--help"])
    if result.returncode != 0:
        return False, "langgraph CLI is not available."

    return True, "langgraph CLI is installed."


def resolve_langsmith_api_key(cli_api_key: str | None = None) -> str | None:
    """Resolve the LangSmith API key from CLI, env, .env, or integrations store."""
    if cli_api_key and cli_api_key.strip():
        return cli_api_key.strip()

    env_key = os.getenv("LANGSMITH_API_KEY", "").strip()
    if env_key:
        return env_key

    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("LANGSMITH_API_KEY="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value

    integration = get_integration("langsmith")
    if isinstance(integration, dict):
        credentials = integration.get("credentials", {})
        if isinstance(credentials, dict):
            maybe_key = credentials.get("api_key", "")
            if isinstance(maybe_key, str) and maybe_key.strip():
                return maybe_key.strip()

    return None


def resolve_deployment_name(cli_name: str | None = None) -> str:
    """Resolve the LangSmith deployment name from CLI, env, .env, or integrations store."""
    if cli_name and cli_name.strip():
        return cli_name.strip()

    env_name = os.getenv("LANGSMITH_DEPLOYMENT_NAME", "").strip()
    if env_name:
        return env_name

    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("LANGSMITH_DEPLOYMENT_NAME="):
                value = line.split("=", 1)[1].strip()
                if value:
                    return value

    integration = get_integration("langsmith")
    if isinstance(integration, dict):
        credentials = integration.get("credentials", {})
        if isinstance(credentials, dict):
            maybe_name = credentials.get("deployment_name", "")
            if isinstance(maybe_name, str) and maybe_name.strip():
                return maybe_name.strip()

    return "open-sre-agent"


def validate_langsmith_api_key(api_key: str) -> tuple[bool, str]:
    """Validate a LangSmith API key against the LangSmith API."""
    try:
        response = httpx.get(
            "https://api.smith.langchain.com/api/v1/sessions",
            headers={"x-api-key": api_key},
            timeout=15.0,
        )

        if response.status_code == 200:
            return True, "LangSmith API key validated."
        if response.status_code == 401:
            return False, "Invalid LangSmith API key."
        if response.status_code == 403:
            return False, "LangSmith API key lacks required permissions for this endpoint."

        if response.is_success:
            return True, "LangSmith API key validated."

        return False, f"LangSmith validation failed with status {response.status_code}."
    except Exception as exc:  # noqa: BLE001
        return False, f"LangSmith validation failed: {exc}"


def persist_langsmith_env(api_key: str, deployment_name: str) -> Path:
    """Persist LangSmith deploy settings to the project .env file."""
    return sync_env_values(
        {
            "LANGSMITH_API_KEY": api_key,
            "LANGSMITH_DEPLOYMENT_NAME": deployment_name,
        }
    )


def run_langsmith_deploy(
    *,
    api_key: str,
    deployment_name: str,
    build_only: bool = False,
) -> tuple[int, str]:
    """Run the LangGraph build/deploy command.

    Note:
        This function assumes prerequisite checks and API-key validation
        have already happened in the caller.
    """
    _ = api_key
    _ = deployment_name

    cmd = ["langgraph", "build"] if build_only else ["langgraph", "deploy"]
    result = _run_command(cmd)
    output = "\n".join(part for part in [result.stdout, result.stderr] if part.strip())
    return result.returncode, output


def extract_deployment_url(output: str) -> str | None:
    """Extract a deployment URL from langgraph CLI output."""
    match = re.search(r"https://[^\s\)\],;:!?'\"]+", output)
    return match.group(0) if match else None
