"""Repository facts read from git's own files, where asking git would cost a process."""

from __future__ import annotations

import re
from collections.abc import Collection
from pathlib import Path

_GITDIR_PREFIX = "gitdir: "
# git's config whitespace is space, tab and carriage return only.
_CONFIG_BLANKS = " \t\r"
_SECTION_RE = re.compile(r'\[([A-Za-z0-9.-]+)(?:[ \t]+"([^"\\]*)")?\][ \t]*(?:[#;].*)?')
_ENTRY_RE = re.compile(r"([A-Za-z][A-Za-z0-9-]*)[ \t]*(?:=[ \t]*(.*))?")
_COMMENT_STARTS = ("#", ";")
_INCLUDE_SECTIONS = frozenset({"include", "includeif"})
# Characters git unquotes, unescapes, cuts at or turns into spaces: such values are left to git.
_VERBATIM_BREAKERS = frozenset('"\\#;\t')
_ORIGIN_SECTION = ("remote", "origin")
_EXTENSIONS_SECTION = ("extensions", None)


def common_git_dir(checkout: Path) -> Path:
    """The git directory shared by every worktree of *checkout*'s repository, resolved.

    A ``.git`` file names the checkout's own git directory (``gitdir: <path>``, relative
    to the checkout), whose ``commondir`` file points at the shared one; a ``.git``
    directory is its own. A pointer that cannot be read ends the resolution there.
    """
    git_dir = _own_git_dir(checkout)
    try:
        shared = (git_dir / "commondir").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        shared = ""
    return (git_dir / shared if shared else git_dir).resolve()


def configured_origin(git_dir: Path, *, rewrites: Collection[str]) -> str | None:
    """``remote.origin.url`` read from *git_dir*'s config file, or None to ask git instead.

    None unless the value is plain: the file is readable, uses no syntax this reader
    leaves to git (quoting, escapes, continuation lines, includes, per-worktree
    config) and names an origin URL that no ``url.<base>.insteadOf`` prefix, from
    *rewrites* or the file itself, would rewrite.
    """
    try:
        text = (git_dir / "config").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    section: tuple[str, str | None] | None = None
    urls: list[str] = []
    own_prefixes: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.strip(_CONFIG_BLANKS)
        if not line or line.startswith(_COMMENT_STARTS):
            continue
        if line.endswith("\\"):
            return None
        header = _SECTION_RE.fullmatch(line)
        if header is not None:
            section = (header.group(1).lower(), header.group(2))
            if section[0] in _INCLUDE_SECTIONS:
                return None
            continue
        entry = _ENTRY_RE.fullmatch(line)
        if entry is None or section is None:
            return None
        key, value = entry.group(1).lower(), entry.group(2)
        if section == _EXTENSIONS_SECTION and key == "worktreeconfig":
            return None
        if section == _ORIGIN_SECTION and key == "url":
            urls.append(value or "")
        elif section[0] == "url" and key == "insteadof":
            own_prefixes.append(value or "")
    url = urls[0] if urls else ""
    if not _verbatim(url) or not all(map(_verbatim, own_prefixes)):
        return None
    if any(url.startswith(prefix) for prefix in (*rewrites, *own_prefixes)):
        return None
    return url


def _own_git_dir(checkout: Path) -> Path:
    dot_git = checkout / ".git"
    if not dot_git.is_file():
        return dot_git
    try:
        first_line = dot_git.read_text(encoding="utf-8", errors="replace").partition("\n")[0]
    except OSError:
        return dot_git
    if not first_line.startswith(_GITDIR_PREFIX):
        return dot_git
    return checkout / first_line[len(_GITDIR_PREFIX) :].strip()


def _verbatim(value: str) -> bool:
    """True when git reads *value* exactly as written."""
    return bool(value) and not _VERBATIM_BREAKERS.intersection(value)


__all__ = ["common_git_dir", "configured_origin"]
