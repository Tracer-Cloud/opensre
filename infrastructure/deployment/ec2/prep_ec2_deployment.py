"""Pre-deploy environment validation for EC2 deployment."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass

import boto3

from config.llm_auth import KEYLESS_PROVIDER_VALUES, SUPPORTED_PROVIDER_VALUES, provider_spec
from config.llm_settings import get_configured_llm_provider, get_llm_provider_api_key_env
from config.local_env import bootstrap_opensre_env, get_project_env_path

_DEPLOY_ENV_EXAMPLE = ".env.deploy.example"

# Fixed user-facing labels only — avoid credential-related substrings in print()
# paths (CodeQL clear-text logging).
_MISSING_LABELS: dict[str, str] = {
    "aws": "AWS account access for EC2 provisioning",
    "telegram_bot": "Telegram gateway bot configuration",
    "llm_provider_invalid": "LLM provider setting (unsupported value)",
    "llm_api": "LLM provider configuration for the selected provider",
}
_WARNING_LABELS: dict[str, str] = {
    "telegram_users": "Telegram allowed-users configuration (recommended)",
    "slack_on_ec2": "Slack is deployed and operated separately, not from this repo",
    "llm_provider_ec2": "LLM provider may not work inside EC2 containers",
}


class DeployEnvValidationError(Exception):
    """Raised after printing the deploy env validation report."""


@dataclass(frozen=True)
class DeployEnvIssue:
    """A deploy env validation issue identified by a stable code."""

    code: str
    env_vars: tuple[str, ...] = ()
    provider: str = ""


def _env_set(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def _aws_credentials_available() -> bool:
    if _env_set("AWS_ROLE_ARN"):
        return True
    if _env_set("AWS_ACCESS_KEY_ID") and _env_set("AWS_SECRET_ACCESS_KEY"):
        return True
    if _env_set("AWS_PROFILE"):
        return True
    try:
        credentials = boto3.Session().get_credentials()
    except Exception:  # noqa: BLE001
        return False
    return credentials is not None


def _collect_messaging_gateway_issues() -> tuple[list[DeployEnvIssue], list[DeployEnvIssue]]:
    """Validate the Telegram deploy env (the EC2 gateway is Telegram-only).

    Slack is deployed and operated separately, not from this repo: Socket Mode
    is single-consumer, so shipping Slack tokens to EC2 would compete with the
    primary Slack gateway. Tokens present in the deploy env are ignored with a
    warning, never shipped.
    """
    missing: list[DeployEnvIssue] = []
    warnings: list[DeployEnvIssue] = []

    if not _env_set("TELEGRAM_BOT_TOKEN"):
        missing.append(DeployEnvIssue("telegram_bot", env_vars=("TELEGRAM_BOT_TOKEN",)))
    elif not _env_set("TELEGRAM_ALLOWED_USERS"):
        warnings.append(DeployEnvIssue("telegram_users", env_vars=("TELEGRAM_ALLOWED_USERS",)))

    if _env_set("SLACK_BOT_TOKEN") or _env_set("SLACK_APP_TOKEN"):
        warnings.append(
            DeployEnvIssue("slack_on_ec2", env_vars=("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"))
        )

    return missing, warnings


def _collect_llm_issues() -> tuple[list[DeployEnvIssue], list[DeployEnvIssue]]:
    missing: list[DeployEnvIssue] = []
    warnings: list[DeployEnvIssue] = []
    provider = get_configured_llm_provider()
    if provider not in SUPPORTED_PROVIDER_VALUES:
        missing.append(DeployEnvIssue("llm_provider_invalid", env_vars=("LLM_PROVIDER",)))
        return missing, warnings

    api_key_env = get_llm_provider_api_key_env(provider)
    if api_key_env and not _env_set(api_key_env):
        missing.append(DeployEnvIssue("llm_api", env_vars=(api_key_env,), provider=provider))
        return missing, warnings

    if provider in KEYLESS_PROVIDER_VALUES:
        spec = provider_spec(provider)
        if spec is not None and spec.credential_kind in {"cli", "local"}:
            warnings.append(DeployEnvIssue("llm_provider_ec2"))
    return missing, warnings


def _collect_deploy_env_issues() -> tuple[list[DeployEnvIssue], list[DeployEnvIssue]]:
    """Return ``(missing_required, warnings)`` for the current process env."""
    bootstrap_opensre_env(override=False)

    missing: list[DeployEnvIssue] = []
    warnings: list[DeployEnvIssue] = []

    if not _aws_credentials_available():
        missing.append(DeployEnvIssue("aws"))

    msg_missing, msg_warnings = _collect_messaging_gateway_issues()
    missing.extend(msg_missing)
    warnings.extend(msg_warnings)

    llm_missing, llm_warnings = _collect_llm_issues()
    missing.extend(llm_missing)
    warnings.extend(llm_warnings)

    return missing, warnings


def _supports_color() -> bool:
    return sys.stdout.isatty() and os.getenv("NO_COLOR", "").strip() == ""


def _highlight(text: str, *, kind: str) -> str:
    if not _supports_color():
        return text
    codes = {"missing": "31", "warn": "33", "label": "1"}
    code = codes.get(kind)
    if code is None:
        return text
    return f"\033[{code}m{text}\033[0m"


def _label_for_issue(issue: DeployEnvIssue, *, warning: bool) -> str:
    labels = _WARNING_LABELS if warning else _MISSING_LABELS
    return labels.get(issue.code, "Deploy environment configuration")


def _format_issue_message(issue: DeployEnvIssue, *, warning: bool) -> str:
    """Format one deploy issue via a code → formatter map (no long if/elif chain)."""
    label = _label_for_issue(issue, warning=warning)
    formatters = {
        "telegram_bot": lambda: (
            f"{issue.env_vars[0]} — API key not set ({label})" if issue.env_vars else label
        ),
        "llm_api": lambda: (
            f"{issue.env_vars[0]} — API key not set "
            f"(required for LLM provider: {issue.provider or 'selected provider'})"
            if issue.env_vars
            else label
        ),
        "aws": lambda: (
            "AWS credentials — not configured "
            "(set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY, AWS_ROLE_ARN, or AWS_PROFILE)"
        ),
        "llm_provider_invalid": lambda: (
            f"{issue.env_vars[0]} — unsupported or missing value ({label})"
            if issue.env_vars
            else label
        ),
        "telegram_users": lambda: (
            f"{issue.env_vars[0]} — not set ({label})" if issue.env_vars else label
        ),
        "slack_on_ec2": lambda: (
            f"{' / '.join(issue.env_vars)} — ignored by the EC2 deploy ({label})"
            if issue.env_vars
            else label
        ),
    }
    formatter = formatters.get(issue.code)
    return formatter() if formatter is not None else label


def _print_deploy_env_report(missing: list[DeployEnvIssue], warnings: list[DeployEnvIssue]) -> None:
    env_path = get_project_env_path()
    print("=" * 60)
    print(_highlight("Deploy environment validation", kind="label"))
    print("=" * 60)

    if missing:
        print()
        print(_highlight("Missing required:", kind="label"))
        for issue in missing:
            message = _format_issue_message(issue, warning=False)
            print(f"  {_highlight('MISSING', kind='missing')}: {message}")

    if warnings:
        print()
        print(_highlight("Recommended:", kind="label"))
        for issue in warnings:
            message = _format_issue_message(issue, warning=True)
            print(f"  {_highlight('WARN', kind='warn')}: {message}")

    if missing or warnings:
        print()
        print(f"Env file: {env_path}")
        print(f"Template: {_DEPLOY_ENV_EXAMPLE}")
        print()

    if missing:
        print(
            f"Deploy aborted: {len(missing)} required environment variable(s) missing. "
            f"Fix the items above and retry."
        )
        print()


def validate_deploy_env() -> None:
    """Fail fast when required deploy environment variables are missing."""
    missing, warnings = _collect_deploy_env_issues()
    if not missing and not warnings:
        return

    _print_deploy_env_report(missing, warnings)

    if missing:
        raise DeployEnvValidationError


def run_lifecycle_main(main: Callable[[], None]) -> None:
    """Run a deploy lifecycle CLI entrypoint with clean env-validation exits."""
    try:
        main()
    except DeployEnvValidationError:
        raise SystemExit(1) from None
