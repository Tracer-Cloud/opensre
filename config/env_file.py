"""Write credentials and settings to their correct storage tier.

OpenSRE persists configuration in three places, and which one a value belongs in
is decided by its **env var name**, not by the caller:

* **secure local storage** (``config.secrets``) — anything
  :func:`is_sensitive_env_key` classifies as a secret (``*_TOKEN``, ``*_KEY``,
  ``*_PASSWORD``, connection strings, …). That is the owner-only file
  ``~/.opensre/credentials.json``.
* **project ``.env``** — public config (URLs, ids, channels, model names) and
  secrets the user or a setup surface writes there
* the integration store — owned by ``integrations.store``, not this module

:func:`sync_env_secret` still requires a sensitive key so a mis-classified
public value does not land in the credentials file. ``.env`` writers accept any
key and leave existing assignments in place.

This lives in ``config/`` — the layer floor — because every setup surface needs
it: the onboarding wizard (``surfaces/``), ``opensre integrations setup``, and
the interactive-shell action tools all persist the same credentials and must
agree on where they go. ``config/local_env.py`` already owns *reading* the
project env file; this owns writing it.
"""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path

from config.env_assignment import env_assignment_key
from config.env_key_sensitivity import is_sensitive_env_key
from config.llm_auth.credentials import delete as delete_provider_auth
from config.llm_auth.credentials import save_api_key
from config.llm_auth.provider_catalog import API_KEY_PROVIDER_ENVS
from config.llm_credentials import delete_credential, save_credential
from config.local_env import get_project_env_path

PROJECT_ENV_PATH = get_project_env_path()


def _persist_env_secret(key: str, value: str) -> bool:
    """Store a secret in secure local storage. False when no tier accepted it."""
    normalized = value.strip()
    provider = next(
        (name for name, env_var in API_KEY_PROVIDER_ENVS.items() if env_var == key),
        "",
    )
    if not normalized:
        if provider:
            delete_provider_auth(provider)
        else:
            delete_credential(key)
        return True
    try:
        if provider:
            save_api_key(provider, normalized)
        else:
            save_credential(key, normalized)
    except (RuntimeError, OSError):
        # RuntimeError covers KeyringUnavailableError, raised when the
        # credentials file refused the write (or storage is disabled).
        return False
    return True


def set_env_value(lines: list[str], key: str, value: str) -> list[str]:
    """Return ``lines`` with ``key`` assigned to ``value`` (appended when absent)."""
    if "\n" in value or "\r" in value:
        raise ValueError(f"Refusing to write {key!r}: values must be a single line.")
    updated: list[str] = []
    replaced = False
    for line in lines:
        if env_assignment_key(line) != key:
            updated.append(line)
            continue
        if not replaced:
            updated.append(f"{key}={value}\n")
            replaced = True

    if not replaced:
        if updated and not updated[-1].endswith("\n"):
            updated[-1] = updated[-1] + "\n"
        updated.append(f"{key}={value}\n")
    return updated


def read_env_lines(target_path: Path) -> list[str]:
    """Read a ``.env`` file into lines, or return ``[]`` when it does not exist."""
    if not target_path.exists():
        return []
    return target_path.read_text(encoding="utf-8").splitlines(keepends=True)


def write_env_lines(target_path: Path, lines: list[str]) -> None:
    """Write ``.env`` lines with owner-only permissions when possible."""
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if os.name != "nt":
            descriptor = os.open(target_path, flags, 0o600)
        else:
            descriptor = os.open(target_path, flags)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as env_file:
            env_file.writelines(lines)
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot write to {target_path}: permission denied. "
            "Ensure you have write access to this file, or run the command as the file owner."
        ) from exc
    if os.name != "nt":
        with suppress(OSError):
            target_path.chmod(0o600)


def _write_or_clear_env_key(target_path: Path, key: str, value: str) -> None:
    """Assign ``key`` in ``.env``, or drop the assignment when *value* is blank."""
    if not value.strip() and not target_path.exists():
        return
    lines = read_env_lines(target_path)
    normalized = value.strip()
    if normalized:
        lines = set_env_value(lines, key, normalized)
    else:
        lines = [line for line in lines if env_assignment_key(line) != key]
    write_env_lines(target_path, lines)


def clear_env_value(key: str, *, env_path: Path | None = None) -> None:
    """Remove ``key`` from the target ``.env`` if the file exists."""
    _write_or_clear_env_key(env_path or PROJECT_ENV_PATH, key, "")


def sync_env_secret(key: str, value: str, *, env_path: Path | None = None) -> None:
    """Persist a sensitive env value in the credentials file and in ``.env``.

    Raises ``RuntimeError`` when local storage cannot hold the secret so
    callers never treat a dropped credential as a successful write.
    """
    if not is_sensitive_env_key(key):
        raise ValueError(f"{key!r} is not classified as sensitive; use sync_env_values instead.")
    if not _persist_env_secret(key, value):
        raise RuntimeError(
            f"Failed to persist {key!r}: the local credentials file "
            "(~/.opensre/credentials.json) could not hold it."
        )
    _write_or_clear_env_key(env_path or PROJECT_ENV_PATH, key, value)


def sync_env_values(
    values: dict[str, str],
    *,
    env_path: Path | None = None,
) -> Path:
    """Write environment values into the target .env file.

    Existing assignments — including secrets — are left in place unless this
    call updates or clears that same key.
    """
    target_path = env_path or PROJECT_ENV_PATH
    lines = read_env_lines(target_path)
    for key, value in values.items():
        lines = set_env_value(lines, key, value)

    write_env_lines(target_path, lines)
    return target_path


__all__ = [
    "PROJECT_ENV_PATH",
    "clear_env_value",
    "env_assignment_key",
    "is_sensitive_env_key",
    "read_env_lines",
    "set_env_value",
    "sync_env_secret",
    "sync_env_values",
    "write_env_lines",
]
