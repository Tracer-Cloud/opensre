"""Shared binary resolution helpers for subprocess-backed CLI adapters."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from functools import lru_cache
from pathlib import Path


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
            timeout=0.3,
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


def resolve_cli_binary(
    *,
    explicit_env_key: str,
    binary_names: Sequence[str],
    fallback_paths: Sequence[str] | Callable[[], Sequence[str]],
    which_resolver: Callable[[str], str | None] = shutil.which,
    runnable_check: Callable[[str], bool] = is_runnable_binary,
) -> str | None:
    """Resolve an executable path from env override, PATH lookup, and fallbacks."""
    explicit = os.getenv(explicit_env_key, "").strip()
    if explicit and runnable_check(explicit):
        return explicit

    for name in binary_names:
        found = which_resolver(name)
        if found:
            return found

    resolved_fallback_paths = fallback_paths() if callable(fallback_paths) else fallback_paths
    for candidate in resolved_fallback_paths:
        if runnable_check(candidate):
            return candidate
    return None
