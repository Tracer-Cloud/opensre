"""Classify an installed Windows bundle without crossing unowned filesystem edges."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from config.constants.installer import (
    WINDOWS_APP_DIR_NAME,
    WINDOWS_BINARY_FILENAME,
    WINDOWS_CURRENT_POINTER_FILENAME,
    WINDOWS_INSTALL_LOCK_FILENAME,
    WINDOWS_LAUNCHER_FILENAME,
    WINDOWS_LAUNCHER_MARKER,
    WINDOWS_LAYOUT_MARKER_FILENAME,
    WINDOWS_LAYOUT_MARKER_TEXT,
    WINDOWS_VERSIONS_DIR_NAME,
)
from surfaces.cli.lifecycle.windows.paths import (
    UnsafeWindowsPathError,
    canonical_existing_path,
    ensure_no_reparse_ancestors,
    ensure_no_reparse_chain,
    ensure_not_reparse,
    ensure_tree_has_no_reparse_points,
    same_windows_file,
    windows_path_exists,
    windows_path_key,
)

_INSTALL_ID_FILE_PATTERN = re.compile(
    r"\A(?P<install_id>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)(?:\r?\n)?\Z"
)


class MalformedWindowsInstallError(RuntimeError):
    """Raised when a version-shaped Windows install cannot prove ownership."""


@dataclass(frozen=True)
class WindowsBinaryInstall:
    """The managed paths a Windows uninstall may act on, or the bare executable."""

    executable: Path
    app_root: Path | None
    launcher: Path | None
    paths: tuple[Path, ...]


def _malformed(message: str, exc: Exception | None = None) -> MalformedWindowsInstallError:
    error = MalformedWindowsInstallError(message)
    if exc is not None:
        error.__cause__ = exc
    return error


def _is_managed_windows_launcher(launcher_path: Path) -> bool:
    if not launcher_path.is_file():
        return False
    try:
        ensure_not_reparse(launcher_path)
        lines = launcher_path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except (OSError, UnsafeWindowsPathError):
        return False
    return (
        len(lines) >= 2
        and lines[0].strip().casefold() == "@echo off"
        and lines[1].strip() == WINDOWS_LAUNCHER_MARKER
    )


def _canonical_executable(exe_path: Path) -> tuple[Path, Path]:
    raw_executable = Path(os.path.abspath(exe_path))
    try:
        if not windows_path_exists(raw_executable):
            raise _malformed(f"Windows executable is missing or unreadable: {raw_executable}")
        # Inspect the raw spelling before resolution so a junction in any ancestor
        # cannot hide an escape. Ordinary aliases such as 8.3 names remain valid.
        ensure_no_reparse_ancestors(raw_executable)
        executable = canonical_existing_path(raw_executable)
        if not executable.is_file():
            raise _malformed(f"Windows executable is missing or unreadable: {raw_executable}")
        return raw_executable, executable
    except UnsafeWindowsPathError as exc:
        raise _malformed(str(exc), exc) from exc


def _windows_versioned_app_root(exe_path: Path) -> tuple[Path, Path] | None:
    raw_executable, executable = _canonical_executable(exe_path)
    if executable.name.casefold() != WINDOWS_BINARY_FILENAME.casefold():
        return None

    version_dir = executable.parent
    versions_dir = version_dir.parent
    app_root = versions_dir.parent
    if (
        versions_dir.name.casefold() == WINDOWS_VERSIONS_DIR_NAME.casefold()
        and app_root.name.casefold() == WINDOWS_APP_DIR_NAME.casefold()
    ):
        try:
            raw_app_root = raw_executable.parent.parent.parent
            ensure_no_reparse_chain(raw_app_root, raw_executable)
            ensure_tree_has_no_reparse_points(app_root)
        except UnsafeWindowsPathError as exc:
            raise _malformed(str(exc), exc) from exc
        return app_root, executable

    if any(
        parent.name.casefold() == WINDOWS_APP_DIR_NAME.casefold() for parent in executable.parents
    ):
        raise _malformed(
            "managed Windows executable path is malformed; expected "
            f"{WINDOWS_APP_DIR_NAME}\\{WINDOWS_VERSIONS_DIR_NAME}\\<install-id>\\"
            f"{WINDOWS_BINARY_FILENAME}"
        )
    return None


def _windows_app_root(exe_path: Path) -> tuple[Path, Path] | None:
    versioned = _windows_versioned_app_root(exe_path)
    if versioned is None:
        return None
    app_root, executable = versioned

    marker = app_root / WINDOWS_LAYOUT_MARKER_FILENAME
    try:
        ensure_no_reparse_chain(app_root, marker)
        marker_text = marker.read_text(encoding="utf-8-sig", errors="replace").strip()
    except (OSError, UnsafeWindowsPathError) as exc:
        raise _malformed(
            f"managed Windows installation marker is missing or unreadable: {marker}", exc
        ) from exc
    if marker_text != WINDOWS_LAYOUT_MARKER_TEXT:
        raise _malformed(f"managed Windows installation marker is invalid: {marker}")

    pointer = app_root / WINDOWS_CURRENT_POINTER_FILENAME
    try:
        ensure_no_reparse_chain(app_root, pointer)
        pointer_text = pointer.read_text(encoding="utf-8-sig", errors="replace")
    except (OSError, UnsafeWindowsPathError) as exc:
        raise _malformed(
            f"managed Windows current-version pointer is missing or unreadable: {pointer}", exc
        ) from exc
    pointer_match = _INSTALL_ID_FILE_PATTERN.fullmatch(pointer_text)
    if pointer_match is None:
        raise _malformed(f"managed Windows current-version pointer is invalid: {pointer}")
    install_id = pointer_match.group("install_id")

    current_executable = app_root / WINDOWS_VERSIONS_DIR_NAME / install_id / WINDOWS_BINARY_FILENAME
    if not current_executable.is_file():
        raise _malformed(f"managed Windows current-version pointer is dangling: {pointer}")
    try:
        ensure_no_reparse_chain(app_root, current_executable)
    except UnsafeWindowsPathError as exc:
        raise _malformed(str(exc), exc) from exc
    if not same_windows_file(current_executable, executable):
        raise _malformed(
            "this OpenSRE process is not the version selected by the managed Windows "
            "current-version pointer; close it and run uninstall from a new PowerShell window"
        )
    return app_root, executable


def classify_windows_binary_install(exe_path: Path | None = None) -> WindowsBinaryInstall:
    requested_executable = Path(exe_path or Path(sys.executable))
    app = _windows_app_root(requested_executable)
    if app is None:
        _, executable = _canonical_executable(requested_executable)
        if executable.name.casefold() != WINDOWS_BINARY_FILENAME.casefold():
            raise _malformed(
                "historical Windows binary name is invalid; expected "
                f"{WINDOWS_BINARY_FILENAME}: {executable}"
            )
        internal = executable.parent / "_internal"
        sibling_app_root = executable.parent / WINDOWS_APP_DIR_NAME
        if internal.is_dir():
            raise _malformed(
                "this executable belongs to an unpacked Windows onedir bundle, not an "
                "install.ps1-managed installation; install OpenSRE with install.ps1 before "
                "running uninstall"
            )
        try:
            sibling_app_root_exists = windows_path_exists(sibling_app_root)
        except UnsafeWindowsPathError as exc:
            raise _malformed(str(exc), exc) from exc
        if sibling_app_root_exists:
            raise _malformed(
                f"an unverified Windows application directory coexists with the flat "
                f"executable: {sibling_app_root}"
            )
        return WindowsBinaryInstall(
            executable=executable,
            app_root=None,
            launcher=None,
            paths=(executable,),
        )

    app_root, executable = app
    install_dir = app_root.parent
    launcher_path = install_dir / WINDOWS_LAUNCHER_FILENAME
    launcher = launcher_path if _is_managed_windows_launcher(launcher_path) else None
    paths: list[Path] = []
    if launcher is not None:
        paths.append(launcher)
    paths.append(app_root)
    install_lock = install_dir / WINDOWS_INSTALL_LOCK_FILENAME
    if install_lock.exists() or install_lock.is_symlink():
        try:
            ensure_not_reparse(install_lock)
        except UnsafeWindowsPathError as exc:
            raise _malformed(str(exc), exc) from exc
        paths.append(install_lock)

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = windows_path_key(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return WindowsBinaryInstall(
        executable=executable,
        app_root=app_root,
        launcher=launcher,
        paths=tuple(deduped),
    )


def windows_binary_install_paths(exe_path: Path | None = None) -> list[Path]:
    """Return only paths whose ownership is proven by the managed layout."""
    return list(classify_windows_binary_install(exe_path).paths)
