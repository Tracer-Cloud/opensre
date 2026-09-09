"""Environment diagnostics behind ``opensre doctor`` and the ``/doctor`` slash command."""

from __future__ import annotations

import os
import platform
import sys
from collections.abc import Callable
from pathlib import Path

from config.version import get_opensre_version


def _run(name: str, fn: Callable[[], tuple[bool, str]]) -> dict[str, str]:
    try:
        ok, detail = fn()
        return {"check": name, "status": "ok" if ok else "warn", "detail": detail}
    except Exception as exc:
        return {"check": name, "status": "error", "detail": str(exc)}


def _check_python_version() -> tuple[bool, str]:
    version = platform.python_version()
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 12):
        return False, f"Python {version} — opensre requires >= 3.12"
    return True, f"Python {version}"


def _check_env_file() -> tuple[bool, str]:
    env_path = os.getenv("OPENSRE_PROJECT_ENV_PATH", ".env")
    path = Path(env_path)
    if not path.exists():
        return False, f"{env_path} not found"
    content = path.read_text(encoding="utf-8", errors="replace")
    lines = [ln for ln in content.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    return True, f"{env_path} ({len(lines)} keys)"


def _check_llm_provider() -> tuple[bool, str]:
    from config.llm_auth.credentials import status as credential_status
    from config.llm_settings import get_configured_llm_provider

    provider = get_configured_llm_provider()
    auth = credential_status(provider)
    if auth.configured and not auth.stale:
        return True, f"provider={provider}, auth={auth.source} ({auth.detail})"
    if auth.stale:
        return False, f"provider={provider}, auth stale ({auth.detail})"
    return False, f"provider={provider}, auth missing ({auth.detail})"


def _check_integrations() -> tuple[bool, str]:
    from integrations.store import list_integrations, resolve_store_path

    path = resolve_store_path()
    if not path.exists():
        return False, f"{path} not found — run 'opensre integrations setup'"
    items = list_integrations()
    if not items:
        return False, "no integrations configured"
    names = [i["service"] for i in items]
    return True, f"{len(items)} configured: {', '.join(names)}"


def _check_buzz_cli() -> tuple[bool, str]:
    """Surface the ``buzz`` CLI's PATH status, but only when Buzz is configured.

    Buzz is an optional integration and ``buzz-cli`` has no package-manager
    distribution (cargo build only), so this check would otherwise warn every
    user who has never touched Buzz.
    """
    from integrations.buzz import resolve_buzz_binary
    from integrations.catalog import resolve_effective_integrations

    if not resolve_effective_integrations().get("buzz"):
        return True, "not configured (optional)"
    path = resolve_buzz_binary()
    if path:
        return True, f"buzz CLI found ({path})"
    return False, (
        "Buzz is configured but the buzz CLI is not on PATH — install with "
        "`cargo install --path crates/buzz-cli` (see docs/messaging/buzz)"
    )


def _check_version_freshness() -> tuple[bool, str]:
    current = get_opensre_version()
    from infrastructure.process.release_version import (
        development_install_doctor_version_detail,
        fetch_latest_version,
        is_update_available,
    )

    dev_detail = development_install_doctor_version_detail(current)
    if dev_detail is not None:
        return True, dev_detail

    try:
        latest = fetch_latest_version()
        if is_update_available(current, latest):
            return False, f"current={current}, latest={latest} — run 'opensre update'"
        return True, f"{current} (up to date)"
    except Exception as exc:
        return True, f"{current} (could not check: {exc})"


def _check_agent_capabilities() -> tuple[bool, str]:
    """Warn when the environment withholds something the agent is told it can do."""
    from infrastructure.safety.sandbox.capabilities import (
        probe_capabilities,
        unavailable_capability_warnings,
    )

    warnings = unavailable_capability_warnings(probe_capabilities())
    if warnings:
        return False, "; ".join(warnings)
    return True, "python, shell, file reading and network are all available"


DOCTOR_CHECKS: tuple[tuple[str, Callable[[], tuple[bool, str]]], ...] = (
    ("python", _check_python_version),
    ("env_file", _check_env_file),
    ("llm_provider", _check_llm_provider),
    ("integrations", _check_integrations),
    ("agent_capabilities", _check_agent_capabilities),
    ("buzz_cli", _check_buzz_cli),
    ("version", _check_version_freshness),
)


def run_doctor_checks() -> list[dict[str, str]]:
    """Every check's ``{"check", "status", "detail"}``; status is ok, warn or error."""
    return [_run(name, fn) for name, fn in DOCTOR_CHECKS]


__all__ = ["DOCTOR_CHECKS", "run_doctor_checks"]
