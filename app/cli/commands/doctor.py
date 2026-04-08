"""Full environment diagnostic command, inspired by ``fly doctor``."""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

import click

from app.cli.context import is_json_output
from app.cli.exit_codes import ERROR, SUCCESS
from app.version import get_version


def _check(name: str, fn: Any) -> dict[str, str]:
    """Run a single diagnostic check and return a result dict."""
    try:
        ok, detail = fn()
        return {"check": name, "status": "ok" if ok else "warn", "detail": detail}
    except Exception as exc:  # noqa: BLE001
        return {"check": name, "status": "error", "detail": str(exc)}


def _check_python_version() -> tuple[bool, str]:
    version = platform.python_version()
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 11):
        return False, f"Python {version} — opensre requires >= 3.11"
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
    provider = os.getenv("LLM_PROVIDER", "").lower() or "not set"
    key_vars = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "nvidia": "NVIDIA_API_KEY",
        "bedrock": "AWS_DEFAULT_REGION",
        "ollama": "OLLAMA_HOST",
    }
    if provider == "not set":
        return False, "LLM_PROVIDER env var is not set"

    expected_key = key_vars.get(provider)
    if expected_key and not os.getenv(expected_key):
        return False, f"provider={provider}, but {expected_key} is not set"
    return True, f"provider={provider}"


def _check_integrations() -> tuple[bool, str]:
    from app.integrations.store import STORE_PATH, list_integrations

    path = Path(str(STORE_PATH))
    if not path.exists():
        return False, f"{STORE_PATH} not found — run 'opensre integrations setup'"
    items = list_integrations()
    if not items:
        return False, "no integrations configured"
    names = [i["service"] for i in items]
    return True, f"{len(items)} configured: {', '.join(names)}"


def _check_version_freshness() -> tuple[bool, str]:
    current = get_version()
    try:
        from app.cli.update import _fetch_latest_version, _is_update_available

        latest = _fetch_latest_version()
        if _is_update_available(current, latest):
            return False, f"current={current}, latest={latest} — run 'opensre update'"
        return True, f"{current} (up to date)"
    except Exception as exc:  # noqa: BLE001
        return True, f"{current} (could not check: {exc})"


def _check_network() -> tuple[bool, str]:
    import httpx

    try:
        resp = httpx.get("https://api.github.com", timeout=5)
        return True, f"github.com reachable (HTTP {resp.status_code})"
    except Exception as exc:  # noqa: BLE001
        return False, f"github.com unreachable: {exc}"


_CHECKS = [
    ("python", _check_python_version),
    ("env_file", _check_env_file),
    ("llm_provider", _check_llm_provider),
    ("integrations", _check_integrations),
    ("version", _check_version_freshness),
    ("network", _check_network),
]

_STATUS_SYMBOLS = {"ok": "\033[1;32m✓\033[0m", "warn": "\033[1;33m!\033[0m", "error": "\033[1;31m✗\033[0m"}


@click.command(name="doctor")
def doctor_command() -> None:
    """Run a full environment diagnostic to surface setup issues."""
    results: list[dict[str, str]] = []
    for name, fn in _CHECKS:
        results.append(_check(name, fn))

    if is_json_output():
        click.echo(json.dumps(results, indent=2))
    else:
        click.echo()
        click.echo("  \033[1mOpenSRE Doctor\033[0m")
        click.echo()
        for r in results:
            sym = _STATUS_SYMBOLS.get(r["status"], "?")
            click.echo(f"  {sym}  {r['check']:<18} {r['detail']}")
        click.echo()

        errors = [r for r in results if r["status"] in ("warn", "error")]
        if errors:
            click.echo(f"  {len(errors)} issue(s) found. Fix them and rerun 'opensre doctor'.")
        else:
            click.echo("  All checks passed.")
        click.echo()

    has_errors = any(r["status"] == "error" for r in results)
    raise SystemExit(ERROR if has_errors else SUCCESS)
