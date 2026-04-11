"""Persistent storage for quickstart wizard selections."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.constants import OPENSRE_HOME_DIR

_VERSION = 1
_EMPTY_CONFIG = {"version": _VERSION, "wizard": {}, "targets": {}, "probes": {}}


def get_store_path() -> Path:
    """Return the default wizard config path."""
    return OPENSRE_HOME_DIR / "opensre.json"


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


def load_named_remotes(path: Path | None = None) -> dict[str, str]:
    """Return all named remotes as ``{name: url}``."""
    data = _load_raw(path)
    remotes: dict[str, Any] = data.get("remote", {}).get("remotes", {})
    return {k: str(v.get("url", "")) for k, v in remotes.items() if v.get("url")}


def save_named_remote(
    name: str,
    url: str,
    *,
    set_active: bool = False,
    source: str = "manual",
    path: Path | None = None,
) -> None:
    """Save a named remote endpoint."""
    store_path = path or get_store_path()
    data = _load_raw(store_path)
    remote_section = data.setdefault("remote", {})
    remotes = remote_section.setdefault("remotes", {})
    remotes[name] = {
        "url": url,
        "source": source,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if set_active:
        remote_section["url"] = url
        remote_section["active_name"] = name
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def set_active_remote(name: str, path: Path | None = None) -> str:
    """Switch the active remote to *name*. Returns the URL."""
    store_path = path or get_store_path()
    data = _load_raw(store_path)
    remotes: dict[str, Any] = data.get("remote", {}).get("remotes", {})
    entry = remotes.get(name)
    if not entry or not entry.get("url"):
        raise KeyError(f"No remote named '{name}'")

    url: str = str(entry["url"])
    remote_section = data.setdefault("remote", {})
    remote_section["url"] = url
    remote_section["active_name"] = name
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return url


def load_active_remote_name(path: Path | None = None) -> str | None:
    """Return the name of the currently active remote, or ``None``."""
    data = _load_raw(path)
    name: str | None = data.get("remote", {}).get("active_name") or None
    return name


def delete_named_remote(name: str, path: Path | None = None) -> None:
    """Remove a named remote from the store."""
    store_path = path or get_store_path()
    data = _load_raw(store_path)
    remotes: dict[str, Any] = data.get("remote", {}).get("remotes", {})
    remotes.pop(name, None)
    if data.get("remote", {}).get("active_name") == name:
        data["remote"].pop("active_name", None)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_remote_ops_config(path: Path | None = None) -> dict[str, str | None]:
    """Return persisted remote ops config values."""
    data = _load_raw(path)
    remote_data = data.get("remote", {})
    if not isinstance(remote_data, dict):
        return {"provider": None, "project": None, "service": None}
    return {
        "provider": str(remote_data.get("provider") or "") or None,
        "project": str(remote_data.get("project") or "") or None,
        "service": str(remote_data.get("service") or "") or None,
    }


def save_remote_ops_config(
    *,
    provider: str,
    project: str | None,
    service: str | None,
    path: Path | None = None,
) -> None:
    """Persist remote ops provider scope to the store."""
    store_path = path or get_store_path()
    data = _load_raw(store_path)
    remote_data = data.setdefault("remote", {})
    remote_data["provider"] = provider
    if project:
        remote_data["project"] = project
    else:
        remote_data.pop("project", None)
    if service:
        remote_data["service"] = service
    else:
        remote_data.pop("service", None)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
