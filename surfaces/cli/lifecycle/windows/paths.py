"""Canonical path and reparse-point checks for Windows lifecycle operations."""

from __future__ import annotations

import os
import stat
from pathlib import Path


class UnsafeWindowsPathError(RuntimeError):
    """Raised when a lifecycle path cannot be proven local and non-reparse."""


def _extended_filesystem_path(path: Path) -> Path:
    """Return a Win32 extended-length spelling for filesystem inspection."""
    absolute = os.path.abspath(path)
    if os.name != "nt" or absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def _without_extended_prefix(path: Path) -> Path:
    value = str(path)
    if value.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + value[8:])
    if value.startswith("\\\\?\\"):
        return Path(value[4:])
    return path


def windows_path_exists(path: Path) -> bool:
    """Check existence through an extended-length path without following links."""
    try:
        _extended_filesystem_path(path).lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise UnsafeWindowsPathError(f"could not inspect Windows path: {path}") from exc
    return True


def _is_reparse_point(path: Path) -> bool:
    filesystem_path = _extended_filesystem_path(path)
    try:
        metadata = filesystem_path.lstat()
    except OSError as exc:
        raise UnsafeWindowsPathError(f"could not inspect Windows path: {path}") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    is_junction = getattr(filesystem_path, "is_junction", None)
    try:
        junction = bool(is_junction()) if is_junction is not None else False
    except OSError as exc:
        raise UnsafeWindowsPathError(f"could not inspect Windows path: {path}") from exc
    return filesystem_path.is_symlink() or junction or bool(attributes & reparse_flag)


def ensure_not_reparse(path: Path) -> None:
    """Reject an existing path when it is a symlink, junction, or other reparse point."""
    if _is_reparse_point(path):
        raise UnsafeWindowsPathError(f"Windows lifecycle path is a reparse point: {path}")


def canonical_existing_path(path: Path) -> Path:
    """Resolve an existing path to its canonical filesystem spelling."""
    try:
        resolved = _extended_filesystem_path(path).resolve(strict=True)
    except OSError as exc:
        raise UnsafeWindowsPathError(f"could not resolve Windows path: {path}") from exc
    ordinary = _without_extended_prefix(resolved)
    return resolved if len(str(ordinary)) >= 240 else ordinary


def windows_path_key(path: Path, *, strict: bool = True) -> str:
    """Return a separator- and case-insensitive canonical key for a Windows path."""
    resolved = canonical_existing_path(path) if strict else path.resolve(strict=False)
    value = str(resolved)
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return value.replace("/", "\\").rstrip("\\").casefold()


def same_windows_file(left: Path, right: Path) -> bool:
    """Compare existing paths by file identity, with canonical spelling as fallback."""
    try:
        return os.path.samefile(left, right)
    except OSError:
        try:
            return windows_path_key(left) == windows_path_key(right)
        except UnsafeWindowsPathError:
            return False


def windows_path_is_within(root: Path, candidate: Path) -> bool:
    """Return whether an existing candidate resolves within an existing root."""
    try:
        root_key = windows_path_key(root)
        candidate_key = windows_path_key(candidate)
    except UnsafeWindowsPathError:
        return False
    return candidate_key == root_key or candidate_key.startswith(root_key + "\\")


def ensure_no_reparse_chain(root: Path, target: Path) -> None:
    """Reject reparse points from ``root`` through the existing ``target`` path."""
    root_absolute = Path(os.path.abspath(root))
    target_absolute = Path(os.path.abspath(target))
    try:
        relative = target_absolute.relative_to(root_absolute)
    except ValueError as exc:
        raise UnsafeWindowsPathError(
            f"Windows lifecycle path escapes its managed root: {target}"
        ) from exc

    current = root_absolute
    ensure_not_reparse(current)
    for part in relative.parts:
        current /= part
        ensure_not_reparse(current)


def ensure_no_reparse_ancestors(target: Path) -> None:
    """Reject reparse points in the complete absolute path to ``target``."""
    target_absolute = Path(os.path.abspath(target))
    if not target_absolute.anchor:
        raise UnsafeWindowsPathError(f"Windows lifecycle path is not absolute: {target}")
    ensure_no_reparse_chain(Path(target_absolute.anchor), target_absolute)


def ensure_tree_has_no_reparse_points(root: Path) -> None:
    """Inspect a complete tree without following links and reject every reparse point."""
    ensure_not_reparse(root)
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(_extended_filesystem_path(directory)) as entries:
                for entry in entries:
                    entry_path = Path(entry.path)
                    ensure_not_reparse(entry_path)
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(entry_path)
                    except OSError as exc:
                        raise UnsafeWindowsPathError(
                            f"could not inspect Windows path: {entry_path}"
                        ) from exc
        except OSError as exc:
            raise UnsafeWindowsPathError(f"could not enumerate Windows path: {directory}") from exc
