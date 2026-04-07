from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx

from app.cli.wizard.env_sync import sync_env_values
from app.cli.wizard.store import get_store_path, load_local_config


def _run_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
    except FileNotFoundError:
        return subprocess.CompletedProcess(cmd, 127, "", f"Command not found: {cmd[0]}")


def is_docker_running() -> tuple[bool, str]:
    if not shutil.which("docker"):
        return False, "Docker is not installed."

    result = _run_command(["docker", "info"])
    if result.returncode != 0:
        return False, "Docker is not running."
    return True, "Docker is running."


def is_langgraph_cli_installed() -> tuple[bool, str]:
    if not shutil.which("langgraph"):
        return False, "langgraph CLI is not installed."
    result = _run_command(["langgraph", "--help"])
    if result.returncode != 0:
        return False, "langgraph CLI is not available."
    return True, "langgraph CLI is installed."


def resolve_langsmith_api_key(cli_api_key: str | None = None) -> str | None:
    import os

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

    store_path = get_store_path()
    if store_path.exists():
        stored = load_local_config(store_path)
        local = stored.get("targets", {}).get("local", {}) if isinstance(stored, dict) else {}
        maybe_key = local.get("langsmith_api_key", "") if isinstance(local, dict) else ""
        if isinstance(maybe_key, str) and maybe_key.strip():
            return maybe_key.strip()

    return None


def resolve_deployment_name(cli_name: str | None = None) -> str:
    import os

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

    return "open-sre-agent"


def validate_langsmith_api_key(api_key: str) -> tuple[bool, str]:
    try:
        response = httpx.get(
            "https://api.smith.langchain.com/api/v1/sessions",
            headers={"x-api-key": api_key},
            timeout=15.0,
        )
        if response.status_code in {200, 401, 403}:
            if response.status_code == 200:
                return True, "LangSmith API key validated."
            return False, "Invalid LangSmith API key."
        if response.is_success:
            return True, "LangSmith API key validated."
        return False, f"LangSmith validation failed with status {response.status_code}."
    except Exception as exc:  # noqa: BLE001
        return False, f"LangSmith validation failed: {exc}"


def persist_langsmith_env(api_key: str, deployment_name: str) -> Path:
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
    persist_langsmith_env(api_key, deployment_name)

    cmd = ["langgraph", "build"] if build_only else ["langgraph", "deploy"]
    result = _run_command(cmd)

    output = "\n".join(part for part in [result.stdout, result.stderr] if part.strip())
    return result.returncode, output


def extract_deployment_url(output: str) -> str | None:
    import re

    match = re.search(r"https://[^\s]+", output)
    return match.group(0) if match else None
