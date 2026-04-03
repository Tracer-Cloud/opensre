"""Analytics transport for the OpenSRE CLI."""

from __future__ import annotations

import contextlib
import json
import os
import platform
import sys
import uuid
from pathlib import Path
from typing import Final

import httpx

from app.analytics.events import Event
from app.cli.wizard.store import get_store_path
from app.version import get_version

_CONFIG_DIR = get_store_path().parent
_ANONYMOUS_ID_PATH = _CONFIG_DIR / "anonymous_id"
_FIRST_RUN_PATH = _CONFIG_DIR / "installed"

_POSTHOG_API_KEY = "phc_zutpVhmQw7oUmMkbawKNdYCKQWjpfASATtf5ywB75W2"
_POSTHOG_HOST = "https://us.i.posthog.com"
_CI_ENV_VARS: Final[tuple[str, ...]] = (
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "BUILDKITE",
    "CIRCLECI",
    "TRAVIS",
    "JENKINS_URL",
    "TEAMCITY_VERSION",
)
_DEBUG_PREFIX = "[telemetry]"
_DEBUG_REDACTED_VALUE = "[REDACTED]"
_SENSITIVE_DEBUG_KEYS: Final[frozenset[str]] = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "client_secret",
        "password",
        "refresh_token",
        "secret",
        "token",
    }
)
# Substrings for keys like user_password / oauth_token (not only exact matches).
_SENSITIVE_KEY_SUBSTRINGS: Final[tuple[str, ...]] = (
    "password",
    "passwd",
    "secret",
    "credential",
    "token",
    "apikey",
    "authorization",
    "bearer",
    "private_key",
    "oauth",
)


def _is_sensitive_key(key: str) -> bool:
    k = key.lower().replace("-", "_")
    if k in _SENSITIVE_DEBUG_KEYS:
        return True
    return any(part in k for part in _SENSITIVE_KEY_SUBSTRINGS)
_SEND_TIMEOUT = 1.0

type PropertyValue = str | bool | int
type Properties = dict[str, PropertyValue]


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "") not in ("", "0", "false", "False")


def _is_ci_environment() -> bool:
    return any(_env_truthy(name) for name in _CI_ENV_VARS)


def _is_test_environment() -> bool:
    return _env_truthy("PYTEST_CURRENT_TEST")


def _is_opted_out() -> bool:
    return (
        _env_truthy("OPENSRE_NO_TELEMETRY")
        or _env_truthy("OPENSRE_ANALYTICS_DISABLED")
        or _env_truthy("DO_NOT_TRACK")
        or _is_test_environment()
        or _is_ci_environment()
    )


def _is_debug_enabled() -> bool:
    return _env_truthy("OPENSRE_TELEMETRY_DEBUG")


def _get_or_create_anonymous_id() -> str:
    try:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if _ANONYMOUS_ID_PATH.exists():
            existing = _ANONYMOUS_ID_PATH.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        new_id = str(uuid.uuid4())
        _ANONYMOUS_ID_PATH.write_text(new_id, encoding="utf-8")
        return new_id
    except OSError:
        return str(uuid.uuid4())


def _touch_once(path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return False
        path.touch()
        return True
    except OSError:
        return False


def _cli_version() -> str:
    return get_version()


def _base_properties() -> Properties:
    return {
        "cli_version": _cli_version(),
        "python_version": platform.python_version(),
        "os_family": platform.system().lower(),
        "os_version": platform.release(),
        "machine_arch": platform.machine().lower(),
        "$process_person_profile": False,
    }


def _build_event_data(event: Event, properties: Properties | None = None) -> dict[str, object] | None:
    if _is_opted_out():
        return None

    return {
        "event": event.value,
        "properties": {
            "distinct_id": _get_or_create_anonymous_id(),
            "$lib": "opensre-cli",
            **_base_properties(),
            **(properties or {}),
        },
    }


def _redact_sensitive_values(value: object) -> object:
    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        for key, nested_value in value.items():
            if _is_sensitive_key(key):
                redacted[key] = _DEBUG_REDACTED_VALUE
            else:
                redacted[key] = _redact_sensitive_values(nested_value)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive_values(item) for item in value]
    return value


def _debug_log(event_data: dict[str, object]) -> None:
    safe = _redact_sensitive_values(event_data)
    print(f"{_DEBUG_PREFIX} {json.dumps(safe, sort_keys=True)}", file=sys.stderr)


def capture(event: Event, properties: Properties | None = None) -> None:
    event_data = _build_event_data(event, properties)
    if event_data is None:
        return

    if _is_debug_enabled():
        _debug_log(event_data)
        return

    payload = {"api_key": _POSTHOG_API_KEY, **event_data}
    with contextlib.suppress(Exception):
        httpx.post(f"{_POSTHOG_HOST}/capture/", json=payload, timeout=_SEND_TIMEOUT).raise_for_status()


def mark_install_detected() -> None:
    if _is_opted_out():
        return
    with contextlib.suppress(OSError):
        _FIRST_RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _FIRST_RUN_PATH.touch(exist_ok=True)


def capture_first_run_if_needed() -> None:
    if _is_opted_out():
        return
    if _touch_once(_FIRST_RUN_PATH):
        capture(
            Event.INSTALL_DETECTED,
            {
                "install_source": "first_run",
                "entrypoint": "opensre",
            },
        )
