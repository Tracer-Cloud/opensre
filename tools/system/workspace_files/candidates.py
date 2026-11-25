"""Which files a listing or a search covers.

In a git work tree that is what git tracks plus what it does not ignore, so a
build copy or a virtual environment is neither listed nor searched: a walk of
this repository reads 72,000 files and reports each match up to sixteen
times, where git names 4,500 files. Outside one, the tree is walked with the
generated directories pruned.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

from tools.system.file_count.count import GENERATED_DIRECTORIES
from tools.system.workspace_files.access import SECRET_DIRECTORIES, holds_secrets

_GIT_TIMEOUT_SEC = 20
_PRUNED_DIRECTORIES = GENERATED_DIRECTORIES | SECRET_DIRECTORIES
#: Characters a glob class must escape to mean themselves in a regex class; ``-`` keeps ranges.
_CLASS_SPECIAL = re.compile(r"[\\\[\]^]")


def glob_matcher(pattern: str) -> Callable[[str], bool]:
    """Match a POSIX path against ``pattern`` the way ``.gitignore`` reads it.

    ``*`` and ``?`` stay inside one directory and ``**/`` spans any number of
    them. A pattern with no ``/`` names files at any depth (``*.py``); one
    with a ``/`` is matched against the whole path (``tests/**/test_*.py``).
    """
    cleaned = pattern.strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    cleaned = cleaned.lstrip("/") or "*"
    if cleaned.endswith("/"):
        # A directory names everything under it.
        cleaned += "**"
    regex = re.compile(_glob_regex(cleaned))
    if "/" in cleaned:
        return lambda path: regex.fullmatch(path) is not None
    return lambda path: regex.fullmatch(path.rsplit("/", 1)[-1]) is not None


def _glob_regex(pattern: str) -> str:
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**/", index):
            parts.append("(?:[^/]+/)*")
            index += 3
        elif pattern.startswith("**", index):
            parts.append(".*")
            index += 2
        elif char == "*":
            parts.append("[^/]*")
            index += 1
        elif char == "?":
            parts.append("[^/]")
            index += 1
        elif char == "[" and "]" in pattern[index + 2 :]:
            end = pattern.index("]", index + 2)
            body = pattern[index + 1 : end]
            negated = body.startswith(("!", "^"))
            members = _CLASS_SPECIAL.sub(r"\\\g<0>", body[1:] if negated else body)
            # Like ``*`` and ``?``, a class never matches the directory separator.
            parts.append(f"(?!/)[{'^' if negated else ''}{members}]")
            index = end + 1
        else:
            parts.append(re.escape(char))
            index += 1
    return "".join(parts)


def workspace_files(scope: Path) -> Iterator[tuple[str, Path]]:
    """``(path relative to scope, file)`` for every readable file under ``scope``, sorted.

    ``scope`` is a directory already resolved inside the working directory and
    outside every secret directory. Symlinks and credential files are left out.
    """
    names = _git_files(scope)
    if names is None:
        names = _walked_files(scope)
    followed: dict[Path, bool] = {}
    # A set: git names an unmerged file once per side of the conflict.
    for name in sorted(set(names)):
        target = scope / name
        if holds_secrets(name) or target.is_symlink():
            continue
        if _reached_through_a_link(target.parent, followed) or not target.is_file():
            continue
        yield name, target


def _reached_through_a_link(directory: Path, followed: dict[Path, bool]) -> bool:
    """Whether a symlink stands in ``directory``'s path below the resolved scope.

    The git index keeps naming the files of a directory that was since replaced
    by a link, and opening one would read wherever the link points: outside the
    working directory, or inside a directory these tools refuse.
    """
    if directory not in followed:
        followed[directory] = directory.resolve() != directory
    return followed[directory]


def _git_files(scope: Path) -> list[str] | None:
    """Tracked and unignored files under ``scope``, or ``None`` outside a git work tree."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=scope,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if listed.returncode != 0:
        return None
    return [name for name in listed.stdout.split("\0") if name]


def _walked_files(scope: Path) -> list[str]:
    names: list[str] = []
    for current, directories, files in os.walk(scope, followlinks=False):
        directories[:] = [name for name in directories if name not in _PRUNED_DIRECTORIES]
        base = Path(current).relative_to(scope)
        names.extend((base / name).as_posix() for name in files)
    return names


__all__ = ["glob_matcher", "workspace_files"]
