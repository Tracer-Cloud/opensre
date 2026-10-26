"""Owner-only atomic storage and process locks for subscription registrations."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from filelock import FileLock

from config.constants.llm import OPENSRE_SUBSCRIPTION_AUTH_DIR_ENV
from config.constants.paths import host_home
from config.llm_auth.subscriptions import SUBSCRIPTION_BY_VALUE
from config.secrets.store import keyring_is_disabled


def subscription_directory(provider: str) -> Path:
    """Resolve one known provider's host-owned credential directory."""
    if provider not in SUBSCRIPTION_BY_VALUE:
        raise ValueError(f"Unknown subscription provider: {provider}")
    root = os.getenv(OPENSRE_SUBSCRIPTION_AUTH_DIR_ENV, "").strip()
    return (Path(root).expanduser() if root else host_home() / "subscriptions") / provider


def subscription_lock(provider: str) -> FileLock:
    """Serialize registration changes and rotating refresh tokens across processes."""
    if keyring_is_disabled():
        raise RuntimeError(
            "Subscription login requires local credential storage; OPENSRE_DISABLE_KEYRING is set."
        )
    directory = subscription_directory(provider)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    return FileLock(str(directory / ".lock"), timeout=30)


def read_store(provider: str) -> dict[str, Any]:
    """Read inside the provider lock; corruption must never replace registrations."""
    path = subscription_directory(provider) / "auth.json"
    if not path.exists():
        return {"profiles": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
            raise ValueError("Invalid store")
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "Subscription credentials cannot be read. Restore or remove the damaged auth.json file."
        ) from exc
    return data


def write_store(provider: str, data: dict[str, Any]) -> None:
    """Atomically replace credentials under the provider lock with Unix mode 0600."""
    directory = subscription_directory(provider)
    fd, temporary = tempfile.mkstemp(prefix=".auth-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, directory / "auth.json")
    finally:
        Path(temporary).unlink(missing_ok=True)
