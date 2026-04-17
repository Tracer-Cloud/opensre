"""Version helpers shared by packaged and frozen entrypoints."""

from __future__ import annotations

import importlib.metadata

PACKAGE_NAME = "opensre"
DEFAULT_VERSION = "2026.4.7"


def get_version() -> str:
    """Return the installed package version, with a build-time fallback."""
    try:
        return importlib.metadata.version(PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return DEFAULT_VERSION
