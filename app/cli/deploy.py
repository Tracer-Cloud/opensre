import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import click
from dotenv import dotenv_values, set_key

LANGSMITH_API_KEY_ENV = "LANGSMITH_API_KEY"
LANGSMITH_DEPLOYMENT_NAME_ENV = "LANGSMITH_DEPLOYMENT_NAME"
LOCAL_ENV_PATH = Path(".env")
LOCAL_CONFIG_PATH = Path.home() / ".opensre" / "opensre.json"


def run_deploy(
    *,
    api_key: str | None = None,
    build_only: bool = False,
    deployment_name: str | None = None,
) -> int:
    """Run the OpenSRE deploy flow for LangSmith."""
    try:
        _check_docker_running()
        _ensure_langgraph_installed()

        resolved_api_key = _resolve_langsmith_api_key(api_key)
        if not resolved_api_key:
            resolved_api_key = click.prompt(
                "Enter your LangSmith API key",
                hide_input=True,
            ).strip()

        _validate_langsmith_api_key(resolved_api_key)

        resolved_deployment_name = (
            deployment_name
            or os.getenv(LANGSMITH_DEPLOYMENT_NAME_ENV)
            or _read_env_value(LOCAL_ENV_PATH, LANGSMITH_DEPLOYMENT_NAME_ENV)
            or _read_local_config_value(LANGSMITH_DEPLOYMENT_NAME_ENV)
            or "open-sre-agent"
        )

        _persist_langsmith_settings(
            api_key=resolved_api_key,
            deployment_name=deployment_name,
        )

        env = os.environ.copy()
        env[LANGSMITH_API_KEY_ENV] = resolved_api_key
        env[LANGSMITH_DEPLOYMENT_NAME_ENV] = resolved_deployment_name

        if build_only:
            click.echo("Building LangGraph image...")
            _run_command(["make", "langgraph-build"], env=env)
            click.echo("Build completed.")
            return 0

        click.echo("Deploying to LangSmith...")
        result = _run_command(
            ["langgraph", "deploy"],
            env=env,
            capture_output=True,
        )

        if result.stdout:
            click.echo(result.stdout.rstrip())
        if result.stderr:
            click.echo(result.stderr.rstrip(), err=True)

        deployment_url = _extract_first_url(result.stdout or "")
        if deployment_url:
            click.echo(f"Deployment URL: {deployment_url}")

        click.echo("Deployment completed.")
        return 0

    except click.ClickException:
        raise
    except subprocess.CalledProcessError as err:
        stderr = (err.stderr or "").strip()
        stdout = (err.stdout or "").strip()
        details = stderr or stdout or str(err)
        raise click.ClickException(details) from err


def _check_docker_running() -> None:
    try:
        subprocess.run(
            ["docker", "info"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError as err:
        raise click.ClickException(
            "Docker is not installed or not available in PATH."
        ) from err
    except subprocess.CalledProcessError as err:
        raise click.ClickException(
            "Docker is not running. Please start Docker and try again."
        ) from err


def _ensure_langgraph_installed() -> None:
    if shutil.which("langgraph") is not None:
        return

    install = click.confirm(
        "langgraph CLI is not installed. Install it now with pip install langgraph-cli?",
        default=True,
    )
    if not install:
        raise click.ClickException(
            "langgraph CLI is required to deploy. Please install it and try again."
        )

    _run_command([sys.executable, "-m", "pip", "install", "langgraph-cli"])
    if shutil.which("langgraph") is None:
        raise click.ClickException(
            "langgraph CLI installation finished, but the command is still not available in PATH."
        )


def _resolve_langsmith_api_key(cli_value: str | None) -> str | None:
    if cli_value:
        return cli_value.strip()

    env_value = os.getenv(LANGSMITH_API_KEY_ENV)
    if env_value:
        return env_value.strip()

    env_file_value = _read_env_value(LOCAL_ENV_PATH, LANGSMITH_API_KEY_ENV)
    if env_file_value:
        return env_file_value.strip()

    local_config_value = _read_local_config_value(LANGSMITH_API_KEY_ENV)
    if local_config_value:
        return local_config_value.strip()

    return None


def _read_env_value(env_path: Path, key: str) -> str | None:
    if not env_path.exists():
        return None

    values = dotenv_values(env_path)
    value = values.get(key)
    if value is None:
        return None
    return str(value)


def _read_local_config_value(key: str) -> str | None:
    if not LOCAL_CONFIG_PATH.exists():
        return None

    try:
        with LOCAL_CONFIG_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None

    value = data.get(key)
    if value is None:
        return None
    return str(value)


def _validate_langsmith_api_key(api_key: str) -> None:
    click.echo("Validating LangSmith API key...")

    env = os.environ.copy()
    env[LANGSMITH_API_KEY_ENV] = api_key

    try:
        _run_command(
            ["make", "check-langsmith-api-key"],
            env=env,
            capture_output=True,
        )
    except subprocess.CalledProcessError as err:
        stderr = (err.stderr or "").strip()
        stdout = (err.stdout or "").strip()
        details = stderr or stdout or "Invalid LangSmith API key."
        raise click.ClickException(details) from err


def _persist_langsmith_settings(api_key: str, deployment_name: str | None) -> None:
    if not LOCAL_ENV_PATH.exists():
        LOCAL_ENV_PATH.write_text("", encoding="utf-8")

    set_key(str(LOCAL_ENV_PATH), LANGSMITH_API_KEY_ENV, api_key)
    if deployment_name:
        set_key(str(LOCAL_ENV_PATH), LANGSMITH_DEPLOYMENT_NAME_ENV, deployment_name)


def _run_command(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=True,
        env=env,
        text=True,
        capture_output=capture_output,
    )


def _extract_first_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s]+", text)
    if not match:
        return None
    return match.group(0)
