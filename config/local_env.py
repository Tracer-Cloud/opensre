"""Local OpenSRE environment bootstrap helpers."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from config.constants.llm import LLM_PROVIDER_ENV
from config.constants.paths import OPENSRE_HOME_DIR, PROJECT_ROOT
from config.env_assignment import env_assignment_key

OPENSRE_PROJECT_ENV_PATH_ENV = "OPENSRE_PROJECT_ENV_PATH"
INSTALLED_ENV_PATH = OPENSRE_HOME_DIR / ".env"
WIZARD_STORE_PATH = OPENSRE_HOME_DIR / "opensre.json"


class _BootstrapState:
    """Mutable holder for the one-shot env bootstrap guard.

    Wrapped in a class so the flag is read/written via attribute access on
    a stable container, avoiding the ``global`` keyword (which CodeQL's
    ``py/unused-global-variable`` rule misreports despite the in-function
    reads).
    """

    bootstrapped: bool = False


def _skip_env_file() -> bool:
    return os.getenv("GRAFANA_CONFIG_SKIP_ENV_FILE") == "1"


def is_frozen_install() -> bool:
    """Return True when running from a bundled executable."""
    return bool(getattr(sys, "frozen", False))


def get_project_env_path() -> Path:
    """Return the env file OpenSRE should load and update for this process."""
    override = os.getenv(OPENSRE_PROJECT_ENV_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    if is_frozen_install():
        return INSTALLED_ENV_PATH
    return PROJECT_ROOT / ".env"


def _load_env_file(path: Path, *, override: bool = False) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        key = env_assignment_key(line)
        if not key:
            continue
        value = line.split("=", 1)[1].strip().strip('"').strip("'")
        if override or key not in os.environ:
            os.environ[key] = value


def _load_wizard_store(path: Path = WIZARD_STORE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _set_if_unset(key: str, value: object) -> None:
    text = str(value or "").strip()
    if not key or not text:
        return
    if key not in os.environ:
        os.environ[key] = text


def _should_apply_wizard_store_defaults() -> bool:
    explicit_env_path = bool(os.getenv(OPENSRE_PROJECT_ENV_PATH_ENV, "").strip())
    return is_frozen_install() and not explicit_env_path


def apply_wizard_store_env_defaults(*, path: Path | None = None) -> None:
    """Fill unset non-secret LLM env keys from ``~/.opensre/opensre.json``."""
    if not _should_apply_wizard_store_defaults():
        return
    payload = _load_wizard_store(path or WIZARD_STORE_PATH)
    targets = payload.get("targets")
    if not isinstance(targets, dict):
        return
    local = targets.get("local")
    if not isinstance(local, dict):
        return

    provider = str(local.get("provider") or "").strip()
    configured_provider = os.environ.get(LLM_PROVIDER_ENV, "").strip()
    if (
        LLM_PROVIDER_ENV in os.environ
        and provider
        and configured_provider.lower() != provider.lower()
    ):
        return

    _set_if_unset(LLM_PROVIDER_ENV, provider)

    model_env = str(local.get("model_env") or "").strip()
    if model_env:
        _set_if_unset(model_env, local.get("model"))


def bootstrap_opensre_env(*, override: bool = False) -> Path:
    """Load the OpenSRE env file and persisted non-secret LLM defaults."""
    path = get_project_env_path()
    if _skip_env_file():
        return path
    _load_env_file(path, override=override)
    apply_wizard_store_env_defaults()
    return path


def bootstrap_opensre_env_once(*, override: bool = False) -> Path:
    """Idempotently load local OpenSRE environment defaults for the process."""
    path = get_project_env_path()
    if not _BootstrapState.bootstrapped:
        path = bootstrap_opensre_env(override=override)
        _BootstrapState.bootstrapped = True
    return path


__all__ = [
    "INSTALLED_ENV_PATH",
    "OPENSRE_PROJECT_ENV_PATH_ENV",
    "PROJECT_ROOT",
    "WIZARD_STORE_PATH",
    "apply_wizard_store_env_defaults",
    "bootstrap_opensre_env",
    "bootstrap_opensre_env_once",
    "get_project_env_path",
    "is_frozen_install",
]
