"""OpenSRE package version for CLI, telemetry, and release reporting."""

from __future__ import annotations

import importlib.metadata
import tomllib
from datetime import UTC, datetime
from pathlib import Path

#: Base public version when no full release string is available.
_DEV_BASE_VERSION = "0.1"
#: Local-version channel for dev builds — the same lineage releases build from.
_DEV_CHANNEL = "main"


def _installed_version() -> str | None:
    try:
        return importlib.metadata.version("opensre")
    except importlib.metadata.PackageNotFoundError:
        return None


def _pyproject_version() -> str | None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    try:
        project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project")
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return None
    if isinstance(project, dict):
        version = project.get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
    return None


def _git_head_sha() -> str:
    """Short sha of the enclosing checkout's HEAD, or ``""`` when git is unreadable."""
    from config.runtime_metadata.build_info import find_git_layout, read_git_head_sha

    layout = find_git_layout()
    if layout is None:
        return ""
    return read_git_head_sha(layout) or ""


def _dev_build_version(base: str) -> str:
    """Expand *base* to the release shape ``base.Y.M.D+main.<sha>`` from git.

    Mirrors the release version so a dev checkout reports the same specific
    build shape. Falls back to *base* when git metadata is unreadable (a
    stripped checkout / wheel without metadata).
    """
    sha = _git_head_sha()
    if not sha:
        return base
    today = datetime.now(tz=UTC)
    return f"{base}.{today.year}.{today.month}.{today.day}+{_DEV_CHANNEL}.{sha}"


def get_opensre_version() -> str:
    """Return the specific build version: the released string, else a git dev build.

    A release build carries the canonical ``0.1.YYYY.M.D+main.<sha>`` string in
    package metadata / ``pyproject`` and is returned verbatim. A dev checkout has
    only the base version, so it is expanded to the same shape from git.
    """
    version = _installed_version() or _pyproject_version()
    if version and "+" in version:
        return version
    return _dev_build_version(version or _DEV_BASE_VERSION)


def get_build_stamp() -> str:
    """Identify the code this installation runs; one build keeps one stamp.

    A release returns its version string. A dev checkout returns
    ``<base>+main.<sha>``: :func:`get_opensre_version` adds the current date
    there, which would read as a new build every day for the same commit.
    """
    version = _installed_version() or _pyproject_version()
    if version and "+" in version:
        return version
    base = version or _DEV_BASE_VERSION
    sha = _git_head_sha()
    return f"{base}+{_DEV_CHANNEL}.{sha}" if sha else base


def get_display_version() -> str:
    """Return the clean marketing version for UI surfaces (e.g. the banner).

    Drops the injected ``.YYYY.M.D+main.<sha>`` build tail that
    :func:`get_opensre_version` carries, leaving the base semver (``0.1``). The
    full build string stays on ``--version``, ``doctor``, and telemetry so
    support and bug reports keep the exact build.
    """
    core = get_opensre_version().split("+", 1)[0]
    kept: list[str] = []
    for part in core.split("."):
        if len(part) == 4 and part.isdigit():  # injected build year — stop before it
            break
        kept.append(part)
    return ".".join(kept) or core
