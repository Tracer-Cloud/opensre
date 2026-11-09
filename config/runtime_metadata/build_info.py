"""Git build-marker detection via filesystem reads (no subprocess).

Resolves the enclosing checkout's git layout — including linked worktrees,
submodule pointer files, and packed refs — and renders a human-readable build
marker: ``""`` for installed wheels, ``dev, <tag> @ <sha>`` for checkouts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_RELEASE_TAG_PATTERN = re.compile(r"^v\d+\.\d+(\.\d+){2,}$")


def resolve_gitdir(candidate: Path) -> Path | None:
    """Return the git directory for ``candidate`` (``.git``), or ``None``.

    Handles both a normal checkout (``.git`` is a directory) and a linked
    worktree / submodule (``.git`` is a file with a ``gitdir: <path>`` line).
    """
    if candidate.is_dir():
        return candidate
    if not candidate.is_file():
        return None
    try:
        content = candidate.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("gitdir:"):
            continue
        target = Path(stripped[len("gitdir:") :].strip())
        if not target.is_absolute():
            target = (candidate.parent / target).resolve()
        return target if target.is_dir() else None
    return None


@dataclass(frozen=True)
class GitLayout:
    """Per-worktree gitdir plus the shared common gitdir.

    In a standard checkout the two are the same directory. In a linked worktree
    (``git worktree add``), ``HEAD`` is per-worktree but ``refs/``, ``packed-refs``,
    and tags live in the primary repo's gitdir named by the worktree's
    ``commondir`` marker file.
    """

    gitdir: Path
    commondir: Path


def resolve_commondir(gitdir: Path) -> Path:
    """Return the shared common gitdir for ``gitdir``.

    Standard checkouts have no ``commondir`` marker; the gitdir is its own
    common dir. Linked worktrees carry a ``commondir`` file with a path
    (relative to the per-worktree gitdir) to the primary repo's gitdir.
    """
    marker = gitdir / "commondir"
    if not marker.is_file():
        return gitdir
    try:
        content = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return gitdir
    if not content:
        return gitdir
    target = Path(content)
    if not target.is_absolute():
        target = (gitdir / target).resolve()
    return target if target.is_dir() else gitdir


def find_git_layout() -> GitLayout | None:
    """Walk up from this file to the enclosing repo's git layout."""
    here = Path(__file__).resolve().parent
    while here.parent != here:
        gitdir = resolve_gitdir(here / ".git")
        if gitdir is not None:
            return GitLayout(gitdir=gitdir, commondir=resolve_commondir(gitdir))
        here = here.parent
    return None


def read_packed_refs(commondir: Path) -> dict[str, str]:
    """Parse ``<commondir>/packed-refs`` into a ``{ref_name: sha}`` map.

    After ``git pack-refs`` the loose files under ``refs/`` disappear and both
    branch heads and tag refs live only here. Peeled tag lines (``^<sha>``) are
    ignored: the non-peeled line already holds the tag object's sha which is
    enough for a build marker.
    """
    packed = commondir / "packed-refs"
    if not packed.is_file():
        return {}
    refs: dict[str, str] = {}
    try:
        content = packed.read_text(encoding="utf-8")
    except OSError:
        return {}
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "^")):
            continue
        sha, _, name = line.partition(" ")
        if sha and name:
            refs[name] = sha
    return refs


def read_ref_sha(layout: GitLayout, ref_name: str) -> str | None:
    """Resolve ``ref_name`` (e.g. ``refs/heads/main``) via loose files + packed-refs.

    Per-worktree refs (bisect/HEAD-like) may live under the worktree gitdir,
    so it's tried first; branches and tags live in the commondir.
    """
    for base in (layout.gitdir, layout.commondir):
        loose = base / ref_name
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip() or None
    return read_packed_refs(layout.commondir).get(ref_name)


_HEAD_SYMREF_PREFIX = "ref: "


def _read_head(layout: GitLayout) -> str | None:
    """Raw ``HEAD`` contents — a ``ref: refs/…`` symref or a detached sha — or ``None``."""
    head_file = layout.gitdir / "HEAD"
    if not head_file.is_file():
        return None
    return head_file.read_text(encoding="utf-8").strip() or None


def read_git_head_sha(layout: GitLayout) -> str | None:
    """Short SHA the working tree currently points at, or ``None``."""
    head = _read_head(layout)
    if head is None:
        return None
    if not head.startswith(_HEAD_SYMREF_PREFIX):
        return head[:7]  # detached HEAD: the sha is HEAD itself
    sha = read_ref_sha(layout, head.removeprefix(_HEAD_SYMREF_PREFIX).strip())
    return sha[:7] if sha else None


def release_tag_sort_key(name: str) -> tuple[int, ...] | None:
    """Numeric tuple for a ``v0.1.YYYY.M.D`` tag; ``None`` if not all-numeric.

    Numeric sort so ``v0.1.2026.10.1`` outranks ``v0.1.2026.9.30`` — a
    lexicographic sort would pick the older tag because ``'9' > '1'`` as ASCII.
    """
    parts = name.removeprefix("v").split(".")
    try:
        return tuple(int(part) for part in parts)
    except ValueError:
        return None


def iter_release_tag_names(commondir: Path) -> set[str]:
    """Release tag names, from loose refs and from ``packed-refs`` combined."""
    names: set[str] = set()
    tags_dir = commondir / "refs" / "tags"
    if tags_dir.is_dir():
        names.update(entry.name for entry in tags_dir.iterdir())
    for ref_name in read_packed_refs(commondir):
        if ref_name.startswith("refs/tags/"):
            names.add(ref_name[len("refs/tags/") :])
    return names


def read_latest_release_tag(commondir: Path) -> str | None:
    """Highest release tag (loose + packed) by numeric ordering."""
    ranked: list[tuple[tuple[int, ...], str]] = []
    for name in iter_release_tag_names(commondir):
        if not _RELEASE_TAG_PATTERN.match(name):
            continue
        key = release_tag_sort_key(name)
        if key is not None:
            ranked.append((key, name))
    if not ranked:
        return None
    return max(ranked)[1]


def detect_build_info() -> str:
    """Human-readable build marker: ``""`` for wheels, ``dev, <tag> @ <sha>`` for checkouts."""
    layout = find_git_layout()
    if layout is None:
        return ""
    tag = read_latest_release_tag(layout.commondir)
    sha = read_git_head_sha(layout)
    if tag and sha:
        return f"dev, {tag} @ {sha}"
    if tag:
        return f"dev, {tag}"
    if sha:
        return f"dev, @ {sha}"
    return "dev"


__all__ = [
    "GitLayout",
    "detect_build_info",
    "find_git_layout",
    "iter_release_tag_names",
    "read_git_head_sha",
    "read_latest_release_tag",
    "read_packed_refs",
    "read_ref_sha",
    "release_tag_sort_key",
    "resolve_commondir",
    "resolve_gitdir",
]
