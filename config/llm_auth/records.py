"""Metadata-file-backed LLM provider auth records.

The records in this file are intentionally non-secret. They exist so status
commands can report local auth state without touching Keychain secrets.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock

from config.constants import OPENSRE_HOME_DIR

_VERSION = 1
_AUTH_RECORD_PREFIX = "provider-auth:"
_LOCK_TIMEOUT_SECONDS = 10.0


def _auth_metadata_path() -> Path:
    override = os.getenv("OPENSRE_LLM_AUTH_METADATA_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    return OPENSRE_HOME_DIR / "llm-auth.json"


def _lock_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".lock")


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with suppress(OSError):
        path.parent.chmod(0o700)


def _empty_store() -> dict[str, object]:
    return {"version": _VERSION, "providers": {}}


def _load_unlocked(path: Path) -> dict[str, object]:
    if not path.exists():
        return _empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_store()
    if not isinstance(data, dict):
        return _empty_store()
    providers = data.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    return {"version": _VERSION, "providers": providers}


def _write_unlocked(path: Path, data: Mapping[str, object]) -> None:
    _ensure_parent(path)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
        with suppress(OSError):
            path.chmod(0o600)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def provider_auth_record_name(provider: str) -> str:
    """Return the stable metadata record name for one LLM provider."""
    normalized = provider.strip().lower()
    if not normalized:
        raise ValueError("provider must not be empty")
    return f"{_AUTH_RECORD_PREFIX}{normalized}"


def _normalize_record(values: Mapping[str, object]) -> dict[str, str]:
    return {
        str(key).strip(): str(value).strip()
        for key, value in values.items()
        if str(key).strip() and str(value).strip()
    }


def save_provider_auth_record(
    *,
    provider: str,
    auth_name: str,
    kind: str,
    source: str,
    detail: str,
    verified: bool = True,
    stale: bool = False,
    env_var: str = "",
) -> None:
    """Persist non-token auth metadata for a provider."""
    provider_value = provider.strip().lower()
    path = _auth_metadata_path()
    _ensure_parent(path)
    with FileLock(str(_lock_path(path)), timeout=_LOCK_TIMEOUT_SECONDS):
        data = _load_unlocked(path)
        providers = data.setdefault("providers", {})
        if not isinstance(providers, dict):
            providers = {}
            data["providers"] = providers
        existing = providers.get(provider_value, {})
        catalog = existing.get("model_catalog", "") if isinstance(existing, dict) else ""
        providers[provider_value] = _normalize_record(
            {
                "model_catalog": catalog,
                "provider": provider_value,
                "auth_name": auth_name,
                "kind": kind,
                "source": source,
                "detail": detail,
                "env_var": env_var,
                "verified": str(bool(verified)).lower(),
                "stale": str(bool(stale)).lower(),
                "updated_at": _utc_now(),
            }
        )
        _write_unlocked(path, data)


def save_provider_auth_record_values(provider: str, values: Mapping[str, object]) -> None:
    """Persist an already-shaped non-secret provider auth record."""
    provider_value = provider.strip().lower()
    path = _auth_metadata_path()
    _ensure_parent(path)
    with FileLock(str(_lock_path(path)), timeout=_LOCK_TIMEOUT_SECONDS):
        data = _load_unlocked(path)
        providers = data.setdefault("providers", {})
        if not isinstance(providers, dict):
            providers = {}
            data["providers"] = providers
        record = _normalize_record({"provider": provider_value, **dict(values)})
        record.setdefault("updated_at", _utc_now())
        providers[provider_value] = record
        _write_unlocked(path, data)


def provider_auth_records() -> dict[str, dict[str, str]]:
    """Read one non-secret connection snapshot under a single metadata lock."""
    path = _auth_metadata_path()
    if not path.exists():
        return {}
    with FileLock(str(_lock_path(path)), timeout=_LOCK_TIMEOUT_SECONDS):
        data = _load_unlocked(path)
    providers = data.get("providers")
    if not isinstance(providers, dict):
        return {}
    return {
        name: {
            key: value
            for key, value in record.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        for name, record in providers.items()
        if isinstance(name, str) and isinstance(record, dict)
    }


def resolve_provider_auth_record(provider: str) -> dict[str, str]:
    """Resolve non-token auth metadata for a provider without reading secrets."""
    return provider_auth_records().get(provider.strip().lower(), {})


def delete_provider_auth_record(provider: str) -> None:
    """Delete provider auth metadata."""
    provider_value = provider.strip().lower()
    path = _auth_metadata_path()
    if not path.exists():
        return
    _ensure_parent(path)
    with FileLock(str(_lock_path(path)), timeout=_LOCK_TIMEOUT_SECONDS):
        data = _load_unlocked(path)
        providers = data.get("providers")
        if isinstance(providers, dict):
            providers.pop(provider_value, None)
        _write_unlocked(path, data)


__all__ = [
    "delete_provider_auth_record",
    "provider_auth_record_name",
    "provider_auth_records",
    "resolve_provider_auth_record",
    "save_provider_auth_record",
    "save_provider_auth_record_values",
]
