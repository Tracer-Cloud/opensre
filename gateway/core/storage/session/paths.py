"""Filesystem paths for the session-binding index (and the legacy index it replaced).

Session bindings map a conversation to a session inside one organization, so
they follow that organization's context root — the mounted volume in a deployed
service.

The team → organization mapping is deliberately absent: it is control-plane
data the webapp already owns, so this process reads it from configuration
rather than keeping a copy.

Not SQLite: the context root is an NFS-backed mount, where SQLite's advisory
locking is unreliable.
"""

from __future__ import annotations

from pathlib import Path

from config.constants.paths import host_home, opensre_home

_GATEWAY_DIR_NAME = "gateway"
_BINDINGS_FILE_NAME = "bindings.json"


def gateway_dir() -> Path:
    """Host-level gateway directory; where a pre-JSON silo kept its binding index."""
    return host_home() / _GATEWAY_DIR_NAME


def bindings_file_path() -> Path:
    """Session bindings for the organization bound to the current turn."""
    return opensre_home() / _GATEWAY_DIR_NAME / _BINDINGS_FILE_NAME


def legacy_binding_dirs(current_dir: Path) -> list[Path]:
    """Directories that may hold a pre-JSON SQLite index.

    Both the directory the bindings file lives in and the host gateway
    directory, so a silo whose context moved onto a mounted volume still finds
    the rows it wrote before the move.
    """
    candidates = [current_dir, gateway_dir()]
    return list(dict.fromkeys(candidates))


__all__ = [
    "bindings_file_path",
    "gateway_dir",
    "legacy_binding_dirs",
]
