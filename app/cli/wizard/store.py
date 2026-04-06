"""Persistent storage for quickstart wizard selections."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_VERSION = 1
_EMPTY_CONFIG = {"version": _VERSION, "wizard": {}, "targets": {}, "probes": {}}


def get_store_path() -> Path:
    """Return the default wizard config path."""
    return Path.home() / ".opensre" / "opensre.json"


def _load_raw(path: Path | None = None) -> dict[str, Any]:
    store_path = path or get_store_path()
    if not store_path.exists():
        return deepcopy(_EMPTY_CONFIG)

    try:
        data = json.loads(store_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return deepcopy(_EMPTY_CONFIG)

    if not isinstance(data, dict):
        return deepcopy(_EMPTY_CONFIG)
    return data


def load_local_config(path: Path | None = None) -> dict[str, Any]:
    """Return the persisted wizard payload for the current user."""
    return _load_raw(path)


def save_local_config(
    *,
    wizard_mode: str,
    provider: str,
    model: str,
    api_key_env: str,
    model_env: str,
    probes: dict[str, dict[str, object]],
    path: Path | None = None,
) -> Path:
    """Persist the local wizard configuration to disk."""
    store_path = path or get_store_path()
    data = _load_raw(store_path)
    timestamp = datetime.now(UTC).isoformat()
    data["version"] = _VERSION
    data["wizard"] = {
        "mode": wizard_mode,
        "configured_target": "local",
        "updated_at": timestamp,
    }
    targets = data.setdefault("targets", {})
    targets["local"] = {
        "provider": provider,
        "model": model,
        "api_key_env": api_key_env,
        "model_env": model_env,
        "updated_at": timestamp,
    }
    data["probes"] = probes

    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return store_path


def load_remote_url(path: Path | None = None) -> str | None:
    """Return the persisted remote agent URL, or ``None`` if not configured."""
    data = _load_raw(path)
    url: str | None = data.get("remote", {}).get("url") or None
    return url


def save_remote_url(url: str, path: Path | None = None) -> None:
    """Persist the remote agent URL to the store."""
    store_path = path or get_store_path()
    data = _load_raw(store_path)
    data.setdefault("remote", {})["url"] = url
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
