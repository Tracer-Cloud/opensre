"""LangSmith deployment helpers for the OpenSRE CLI."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx

from app.cli.wizard.store import get_store_path

DEFAULT_DEPLOYMENT_NAME = "open-sre-agent"
LANGSMITH_API_KEY_ENV = "LANGSMITH_API_KEY"
LANGSMITH_DEPLOYMENT_NAME_ENV = "LANGSMITH_DEPLOYMENT_NAME"


def _run_command(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command and return the completed process."""
    try:
        return subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=timeout,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=127,
            stdout="",
            stderr=f"Command not found: {cmd[0]}",
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=124,
            stdout=stdout,
            stderr=stderr or f"Command timed out: {' '.join(cmd)}",
        )


def is_langgraph_cli_installed() -> tuple[bool, str]:
    """Check whether the langgraph CLI is installed and callable."""
    if not shutil.which("langgraph"):
        return False, "langgraph CLI is not installed."

    result = _run_command(["langgraph", "--help"], timeout=15.0)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        if detail:
            return False, f"langgraph CLI is not available: {detail}"
        return False, "langgraph CLI is not available."

    return True, "langgraph CLI is installed."


def _project_env_path() -> Path:
    """Resolve the project .env path, honoring the test override env var."""
    override = os.getenv("OPENSRE_PROJECT_ENV_PATH", "").strip()
    if override:
        return Path(override)
    return Path(".env")


def _read_env_value(env_path: Path, key: str) -> str | None:
    """Read a single KEY=value entry from a .env-style file."""
    if not env_path.exists():
        return None

    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not stripped.startswith(f"{key}="):
                continue

            value = stripped.split("=", 1)[1].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            return value or None
    except OSError:
        return None

    return None


def _load_opensre_store() -> dict[str, Any]:
    """Load ~/.opensre/opensre.json, returning an empty dict on failure."""
    store_path = get_store_path()
    if not store_path.exists():
        return {}

    try:
        payload = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    return payload if isinstance(payload, dict) else {}


def _read_store_langsmith_api_key() -> str | None:
    """Best-effort lookup for a LangSmith API key from opensre.json."""
    data = _load_opensre_store()

    # Flexible fallback lookup to avoid coupling to a single schema.
    candidates: list[Any] = [
        data.get("langsmith"),
        data.get("deploy"),
        data.get("wizard"),
        data.get("targets"),
        data.get("remote"),
    ]

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        maybe_key = candidate.get("api_key") or candidate.get(LANGSMITH_API_KEY_ENV)
        if isinstance(maybe_key, str) and maybe_key.strip():
            return maybe_key.strip()

        credentials = candidate.get("credentials")
        if isinstance(credentials, dict):
            nested_key = credentials.get("api_key") or credentials.get(LANGSMITH_API_KEY_ENV)
            if isinstance(nested_key, str) and nested_key.strip():
                return nested_key.strip()

    return None


def _read_store_deployment_name() -> str | None:
    """Best-effort lookup for a deployment name from opensre.json."""
    data = _load_opensre_store()

    candidates: list[Any] = [
        data.get("langsmith"),
        data.get("deploy"),
        data.get("wizard"),
        data.get("targets"),
        data.get("remote"),
    ]

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        maybe_name = candidate.get("deployment_name") or candidate.get(
            LANGSMITH_DEPLOYMENT_NAME_ENV
        )
        if isinstance(maybe_name, str) and maybe_name.strip():
            return maybe_name.strip()

        credentials = candidate.get("credentials")
        if isinstance(credentials, dict):
            nested_name = credentials.get("deployment_name") or credentials.get(
                LANGSMITH_DEPLOYMENT_NAME_ENV
            )
            if isinstance(nested_name, str) and nested_name.strip():
                return nested_name.strip()

    return None


def resolve_langsmith_api_key(cli_api_key: str | None = None) -> str | None:
    """Resolve the LangSmith API key from CLI arg, env, .env, or opensre store."""
    if cli_api_key and cli_api_key.strip():
        return cli_api_key.strip()

    env_key = os.getenv(LANGSMITH_API_KEY_ENV, "").strip()
    if env_key:
        return env_key

    env_file_key = _read_env_value(_project_env_path(), LANGSMITH_API_KEY_ENV)
    if env_file_key:
        return env_file_key

    return _read_store_langsmith_api_key()


def resolve_deployment_name(cli_name: str | None = None) -> str:
    """Resolve the deployment name from CLI arg, env, .env, or opensre store."""
    if cli_name and cli_name.strip():
        return cli_name.strip()

    env_name = os.getenv(LANGSMITH_DEPLOYMENT_NAME_ENV, "").strip()
    if env_name:
        return env_name

    env_file_name = _read_env_value(_project_env_path(), LANGSMITH_DEPLOYMENT_NAME_ENV)
    if env_file_name:
        return env_file_name

    store_name = _read_store_deployment_name()
    if store_name:
        return store_name

    return DEFAULT_DEPLOYMENT_NAME


def validate_langsmith_api_key(api_key: str) -> tuple[bool, str]:
    """Validate a LangSmith API key against the LangSmith API."""
    try:
        response = httpx.get(
            "https://api.smith.langchain.com/api/v1/sessions",
            headers={"x-api-key": api_key},
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        return False, f"LangSmith validation request failed: {exc}"

    if response.status_code == 200:
        return True, "LangSmith API key validated."

    if response.status_code == 401:
        return False, "Invalid LangSmith API key."

    if response.status_code == 403:
        return True, "LangSmith API key appears valid, but the validation endpoint is permission-limited."

    if response.is_success:
        return True, f"LangSmith API key validated with status {response.status_code}."

    return False, f"LangSmith validation failed with status {response.status_code}."


def persist_langsmith_env(api_key: str, deployment_name: str) -> Path:
    """Persist LangSmith settings to the project .env file."""
    env_path = _project_env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    if env_path.exists():
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []

    updates = {
        LANGSMITH_API_KEY_ENV: api_key,
        LANGSMITH_DEPLOYMENT_NAME_ENV: deployment_name,
    }

    seen: set[str] = set()
    updated_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            updated_lines.append(line)
            continue

        key, _value = line.split("=", 1)
        normalized_key = key.strip()
        if normalized_key in updates:
            updated_lines.append(f"{normalized_key}={updates[normalized_key]}")
            seen.add(normalized_key)
        else:
            updated_lines.append(line)

    for key, value in updates.items():
        if key not in seen:
            updated_lines.append(f"{key}={value}")

    content = "\n".join(updated_lines).rstrip() + "\n"
    env_path.write_text(content, encoding="utf-8")
    return env_path


def run_langsmith_deploy(
    *,
    api_key: str,
    deployment_name: str,
    build_only: bool = False,
) -> tuple[int, str]:
    """Run the LangGraph build/deploy command."""
    env = {
        **os.environ,
        LANGSMITH_API_KEY_ENV: api_key,
        LANGSMITH_DEPLOYMENT_NAME_ENV: deployment_name,
    }

    cmd = ["langgraph", "build"] if build_only else ["langgraph", "deploy"]
    result = _run_command(cmd, env=env, timeout=1800.0)

    output_parts = [part.strip() for part in [result.stdout, result.stderr] if part and part.strip()]
    output = "\n".join(output_parts)
    return int(result.returncode), output


def extract_deployment_url(output: str) -> str | None:
    """Extract a deployment URL from langgraph CLI output."""
    match = re.search(r"https://[^\s\)\]\},;\'\"]+", output)
    return match.group(0) if match else None
