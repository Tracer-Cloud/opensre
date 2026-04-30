"""Shared binary resolution helpers for subprocess-backed CLI adapters.

Key public API
--------------

resolve_cli_binary(...)
    Locate an executable using three-stage resolution:
    1. Explicit ``*_BIN`` env override (e.g. ``CODEX_BIN``) — only used when the
       path is runnable; logs a WARNING and falls through otherwise.
    2. ``shutil.which`` PATH lookup for platform-specific binary names.
    3. Conventional install-location fallbacks (npm, volta, pnpm, Homebrew, etc.).

diagnose_binary_path(path) -> str | None
    Return a human-readable reason why *path* is not usable, or ``None`` when it
    is fine.  Distinguishes the following states so callers can surface actionable
    messages to users:

    +---------------------------------+----------------------------------------------------+
    | Path state                      | Returned message (excerpt)                         |
    +=================================+====================================================+
    | Broken symlink                  | "'<path>' is a broken symlink (points to '<target>'). Remove or fix it." |
    +---------------------------------+----------------------------------------------------+
    | Does not exist                  | "'<path>' does not exist."                         |
    +---------------------------------+----------------------------------------------------+
    | Exists but is not a file        | "'<path>' is not a file."                          |
    +---------------------------------+----------------------------------------------------+
    | File but not executable (Unix)  | "'<path>' is not executable. Run: chmod +x <path>" |
    +---------------------------------+----------------------------------------------------+
    | File with wrong extension (Win) | "'<path>' is not a recognised executable (expected .cmd, .exe, .ps1, or .bat)." |
    +---------------------------------+----------------------------------------------------+
    | Valid runnable binary           | ``None``                                           |
    +---------------------------------+----------------------------------------------------+

    On Windows the executable check uses file extension (``.cmd``, ``.exe``,
    ``.ps1``, ``.bat``) mirroring ``is_runnable_binary``, so both functions
    accept and reject the same set of paths.

is_runnable_binary(path) -> bool
    Low-level predicate used by ``resolve_cli_binary`` and the CLI wizard.
    Prefer ``diagnose_binary_path`` when a user-facing message is needed.

Platform notes
--------------

* Windows binary names include ``.cmd``, ``.exe``, ``.ps1``, ``.bat`` suffixes;
  ``candidate_binary_names`` returns all four for a given base name.
* ``npm_prefix_bin_dirs`` is ``@lru_cache``-d — call ``.cache_clear()`` in tests
  that vary ``NPM_CONFIG_PREFIX`` or ``sys.platform``.
* ``diagnose_binary_path`` reads the symlink target via ``Path.readlink()``
  (Python ≥ 3.9) for a more actionable error message; falls back silently on
  older hosts or permission errors.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


def candidate_binary_names(binary_name: str) -> tuple[str, ...]:
    """Return platform-specific executable names for a CLI binary."""
    if sys.platform == "win32":
        return (
            f"{binary_name}.cmd",
            f"{binary_name}.exe",
            f"{binary_name}.ps1",
            f"{binary_name}.bat",
        )
    return (binary_name,)


def _append_candidate_paths(
    candidates: list[str], directory: Path | str, names: tuple[str, ...]
) -> None:
    base = str(directory).strip()
    if not base:
        return
    root = Path(base).expanduser()
    for name in names:
        candidates.append(str(root / name))


@lru_cache(maxsize=1)
def npm_prefix_bin_dirs() -> tuple[str, ...]:
    """Resolve npm global bin directories from env and npm config."""
    env_prefix = os.getenv("NPM_CONFIG_PREFIX", "").strip()
    if not env_prefix:
        # npm often exports lowercase `npm_config_prefix`; accept any casing.
        for key, value in os.environ.items():
            if key.lower() == "npm_config_prefix":
                env_prefix = value.strip()
                if env_prefix:
                    break
    if env_prefix:
        if sys.platform == "win32":
            return (str(Path(env_prefix).expanduser()),)
        return (str(Path(env_prefix).expanduser() / "bin"),)

    try:
        proc = subprocess.run(
            ["npm", "config", "get", "prefix"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()

    prefix = (proc.stdout or "").strip()
    if proc.returncode != 0 or not prefix:
        return ()

    if sys.platform == "win32":
        return (str(Path(prefix).expanduser()),)
    return (str(Path(prefix).expanduser() / "bin"),)


def default_cli_fallback_paths(binary_name: str) -> list[str]:
    """Build common fallback install locations for a CLI binary."""
    home = Path.home()
    names = candidate_binary_names(binary_name)
    candidates: list[str] = []

    if sys.platform == "win32":
        _append_candidate_paths(candidates, Path(os.getenv("APPDATA", "")) / "npm", names)
        _append_candidate_paths(
            candidates,
            Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / binary_name,
            names,
        )
    else:
        if sys.platform == "darwin":
            _append_candidate_paths(candidates, "/opt/homebrew/bin", names)
        _append_candidate_paths(candidates, "/usr/local/bin", names)
        _append_candidate_paths(candidates, home / ".local/bin", names)
        _append_candidate_paths(candidates, home / ".npm-global/bin", names)
        _append_candidate_paths(candidates, home / ".volta/bin", names)
        _append_candidate_paths(candidates, os.getenv("PNPM_HOME", ""), names)
        xdg_data_home = os.getenv("XDG_DATA_HOME", "").strip()
        if xdg_data_home:
            _append_candidate_paths(candidates, Path(xdg_data_home) / "pnpm", names)

    for npm_dir in npm_prefix_bin_dirs():
        _append_candidate_paths(candidates, npm_dir, names)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = str(Path(candidate).expanduser())
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def is_runnable_binary(path: str) -> bool:
    """Return True when a path points to an executable binary/script."""
    p = Path(path)
    if not p.is_file():
        return False
    if sys.platform == "win32":
        return p.suffix.lower() in {".cmd", ".exe", ".ps1", ".bat"} or os.access(p, os.X_OK)
    return os.access(p, os.X_OK)


def diagnose_binary_path(path: str) -> str | None:
    """Return a human-readable reason why *path* is not runnable, or None if it is.

    Distinguishes broken symlinks from missing files so callers can surface a
    more actionable message than a generic "not found".
    """
    p = Path(path)
    if p.is_symlink() and not p.exists():
        target = ""
        with contextlib.suppress(OSError, AttributeError):
            target = f" (points to '{p.readlink()}')"
        return f"'{path}' is a broken symlink{target}. Remove or fix it."
    if not p.exists():
        return f"'{path}' does not exist."
    if not p.is_file():
        return f"'{path}' is not a file."
    if sys.platform == "win32":
        if p.suffix.lower() not in {".cmd", ".exe", ".ps1", ".bat"} and not os.access(p, os.X_OK):
            return f"'{path}' is not a recognised executable (expected .cmd, .exe, .ps1, or .bat)."
    elif not os.access(p, os.X_OK):
        return f"'{path}' is not executable. Run: chmod +x {path}"
    return None


def resolve_cli_binary(
    *,
    explicit_env_key: str,
    binary_names: Sequence[str],
    fallback_paths: Sequence[str] | Callable[[], Sequence[str]],
    which_resolver: Callable[[str], str | None] | None = None,
    runnable_check: Callable[[str], bool] | None = None,
) -> str | None:
    """Resolve an executable path from env override, PATH lookup, and fallbacks.

    ``which_resolver`` and ``runnable_check`` default to ``shutil.which`` and
    ``is_runnable_binary`` respectively.  They are looked up at *call time* (not
    bound as default parameter values) so that test patches on this module's
    ``shutil.which`` / ``is_runnable_binary`` take effect without callers having
    to pass explicit overrides.
    """
    _which = which_resolver if which_resolver is not None else shutil.which
    _runnable = runnable_check if runnable_check is not None else is_runnable_binary

    explicit = os.getenv(explicit_env_key, "").strip()
    if explicit:
        if _runnable(explicit):
            return explicit
        reason = diagnose_binary_path(explicit)
        logger.warning(
            "%s is set but unusable — falling back to PATH/defaults. %s",
            explicit_env_key,
            reason or "Not a runnable file.",
        )

    for name in binary_names:
        found = _which(name)
        if found:
            return found

    resolved_fallback_paths = fallback_paths() if callable(fallback_paths) else fallback_paths
    for candidate in resolved_fallback_paths:
        if _runnable(candidate):
            return candidate
    return None
