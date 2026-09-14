"""Finish a merge that stopped on conflicts after a resolver edited the files.

Any caller that hands conflicted files to a coding agent uses the same three
steps: snapshot the stopped merge, check what the agent left unresolved, and
stage plus commit the merge. The caller decides what to do when files remain
unresolved (abort, or keep the merge in progress for a person).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Final

from integrations.git.errors import MERGE_FAILED, GitCommandError
from integrations.git.local import (
    _run_git,
    changed_since_baseline,
    current_branch,
    file_fingerprints,
    staged_paths,
    unstage_paths,
)
from integrations.git.merge import (
    ConflictedPath,
    commit_merge,
    commit_parents,
    describe_conflicts,
    head_sha,
    paths_with_conflict_markers,
    stage_paths,
    unmerged_paths,
)

# Lockfiles are regenerated from their manifest, never merged by hand.
LOCKFILE_NAMES: Final = frozenset(
    {
        "Cargo.lock",
        "Gemfile.lock",
        "go.sum",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "yarn.lock",
    }
)


@dataclass(frozen=True)
class ConflictHunk:
    """One ``<<<<<<< … >>>>>>>`` block: the two competing versions of the same lines."""

    ours: tuple[str, ...]
    theirs: tuple[str, ...]
    start: int
    end: int


@dataclass(frozen=True)
class MergeConflicts:
    """The unmerged paths of a stopped merge and their content at that moment."""

    ours: str
    theirs: str
    paths: tuple[ConflictedPath, ...]
    content: Mapping[str, str]
    conflicted_lines: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    head: str = ""
    staged_before: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(conflict.path for conflict in self.paths)

    def hunks(self, path: str) -> tuple[ConflictHunk, ...]:
        return parse_conflict_hunks(self.conflicted_lines.get(path, ()))


def merge_conflicts(workspace: str, *, ours: str, theirs: str) -> MergeConflicts:
    """Snapshot the conflicts of the merge in progress in *workspace*."""
    conflicts = describe_conflicts(workspace, ours=ours, theirs=theirs)
    names = [conflict.path for conflict in conflicts]
    return MergeConflicts(
        ours=ours,
        theirs=theirs,
        paths=tuple(conflicts),
        content=file_fingerprints(workspace, names),
        conflicted_lines={path: _read_lines(workspace, path) for path in names},
        head=head_sha(workspace),
        staged_before=tuple(staged_paths(workspace)),
    )


def parse_conflict_hunks(lines: Sequence[str]) -> tuple[ConflictHunk, ...]:
    """The conflict blocks of a file with markers; a diff3 base section is skipped."""
    hunks: list[ConflictHunk] = []
    ours: list[str] = []
    theirs: list[str] = []
    start = -1
    side: list[str] | None = None
    for index, line in enumerate(lines):
        if line.startswith("<<<<<<< "):
            ours, theirs, start = [], [], index
            side = ours
        elif start >= 0 and line.startswith("||||||| "):
            side = None
        elif start >= 0 and line == "=======":
            side = theirs
        elif start >= 0 and line.startswith(">>>>>>> "):
            hunks.append(ConflictHunk(tuple(ours), tuple(theirs), start=start, end=index + 1))
            start, side = -1, None
        elif side is not None:
            side.append(line)
    return tuple(hunks)


@dataclass(frozen=True)
class HunkComparison:
    """One conflict hunk next to what the working tree holds for it now."""

    path: str
    ours: tuple[str, ...]
    theirs: tuple[str, ...]
    result: tuple[str, ...] | None
    file_removed: bool = False


def compare_hunks(
    workspace: str, conflicts: MergeConflicts, *, revision: str | None = None
) -> list[HunkComparison]:
    """Each conflict hunk with its resolution as found in the working tree or in *revision*.

    ``result`` is ``None`` while the file still carries markers or cannot be
    read; ``file_removed`` says the resolution deleted the file, which is
    distinct from a hunk resolved to nothing in a file that still exists.
    """
    comparisons: list[HunkComparison] = []
    for path in conflicts.names:
        before = conflicts.conflicted_lines.get(path, ())
        exists, after = (
            _content_in_tree(workspace, path)
            if revision is None
            else _content_at(workspace, revision, path)
        )
        lines = after or ()
        readable = after is not None
        resolved = readable and not any(line.startswith(_CONFLICT_MARKERS) for line in lines)
        opcodes = SequenceMatcher(None, before, lines, autojunk=False).get_opcodes()
        for hunk in parse_conflict_hunks(before):
            result = _resolved_region(lines, opcodes, hunk) if resolved else None
            comparisons.append(
                HunkComparison(path, hunk.ours, hunk.theirs, result, file_removed=not exists)
            )
    return comparisons


def _content_at(workspace: str, revision: str, path: str) -> tuple[bool, tuple[str, ...] | None]:
    """(whether *revision* has *path*, its lines or None when it cannot be read)."""
    if _run_git(workspace, "cat-file", "-e", f"{revision}:{path}").returncode != 0:
        return False, ()
    result = _run_git(workspace, "show", f"{revision}:{path}")
    return True, tuple(result.stdout.splitlines()) if result.returncode == 0 else None


def _content_in_tree(workspace: str, path: str) -> tuple[bool, tuple[str, ...] | None]:
    file = os.path.join(workspace, path)
    if not os.path.isfile(file):
        return False, ()
    return True, _read_lines(workspace, path)


def _resolved_region(
    after: Sequence[str], opcodes: Sequence[tuple[str, int, int, int, int]], hunk: ConflictHunk
) -> tuple[str, ...]:
    """Lines of *after* that replaced the marker block ``[hunk.start, hunk.end)``."""
    region: list[str] = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "insert":
            if hunk.start <= i1 <= hunk.end:
                region.extend(after[j1:j2])
            continue
        lo, hi = max(i1, hunk.start), min(i2, hunk.end)
        if lo >= hi:
            continue
        if tag == "equal":
            region.extend(after[j1 + (lo - i1) : j1 + (hi - i1)])
        elif tag == "replace":
            region.extend(after[j1:j2])
    return tuple(region)


_CONFLICT_MARKERS = ("<<<<<<< ", ">>>>>>> ")


def _read_lines(workspace: str, path: str) -> tuple[str, ...]:
    file = os.path.join(workspace, path)
    if not os.path.isfile(file):
        return ()
    with open(file, encoding="utf-8", errors="replace") as handle:
        return tuple(handle.read().splitlines())


def unresolved_conflicts(workspace: str, conflicts: MergeConflicts) -> list[ConflictedPath]:
    """Conflicted paths the resolver left with markers, never touched, or removed.

    A content conflict always starts with markers, so a file without them was
    edited; one whose working-tree file is gone while its index entry is still
    unmerged was not resolved, only removed. Delete/modify conflicts carry no
    markers, so an untouched one is judged by its content fingerprint being
    unchanged since the merge stopped while its index entry is still unmerged;
    a kept file the resolver staged as-is counts as resolved.
    """
    marked = set(paths_with_conflict_markers(workspace, conflicts.names))
    still_unmerged = set(unmerged_paths(workspace))
    current = file_fingerprints(workspace, conflicts.names)
    unresolved: list[ConflictedPath] = []
    for conflict in conflicts.paths:
        fingerprint = current.get(conflict.path, "")
        untouched = fingerprint == conflicts.content.get(conflict.path, "")
        removed_or_untouched = fingerprint == "" or (bool(conflict.deleted_on) and untouched)
        if conflict.path in marked or (conflict.path in still_unmerged and removed_or_untouched):
            unresolved.append(conflict)
    return unresolved


def conclude_merge(
    workspace: str, conflicts: MergeConflicts, *, baseline: Mapping[str, str]
) -> str:
    """Stage the resolver's edits plus the conflicted paths and commit the merge.

    *baseline* fingerprints the files that were already dirty before the
    resolver ran, so a person's unrelated work in progress is not swept into
    the merge commit; anything the resolver staged beyond its edits, the
    conflicted paths, and what git had staged when the merge stopped is put
    back out of the index first. Raises ``GitCommandError`` when an unmerged
    path remains.
    """
    edited = changed_since_baseline(workspace, baseline=baseline)
    stage_paths(workspace, edited)
    stage_paths(workspace, conflicts.names)
    intended = {*conflicts.staged_before, *conflicts.names, *edited}
    unstage_paths(workspace, [path for path in staged_paths(workspace) if path not in intended])
    remaining = unmerged_paths(workspace)
    if remaining:
        raise GitCommandError(
            MERGE_FAILED,
            f"Conflicts remain in {', '.join(remaining)}; the merge was not committed.",
        )
    return commit_merge(workspace)


@dataclass(frozen=True)
class ResolvedPath:
    """How one conflicted path ended up in the merge commit."""

    path: str
    resolution: str

    def __str__(self) -> str:
        return f"{self.path}: {self.resolution}"


def describe_resolutions(
    workspace: str, merge_sha: str, conflicts: MergeConflicts
) -> list[ResolvedPath]:
    """Say, per conflicted path, whether the merge kept ours, took theirs, or combined both."""
    return [
        ResolvedPath(
            path=conflict.path,
            resolution=_resolution(workspace, merge_sha, conflict.path, conflicts),
        )
        for conflict in conflicts.paths
    ]


def _resolution(workspace: str, merge_sha: str, path: str, conflicts: MergeConflicts) -> str:
    exists = _run_git(workspace, "cat-file", "-e", f"{merge_sha}:{path}").returncode == 0
    if not exists:
        return "removed"
    same_as_ours = _same_in(workspace, f"{merge_sha}^1", merge_sha, path)
    same_as_theirs = _same_in(workspace, f"{merge_sha}^2", merge_sha, path)
    if same_as_ours and same_as_theirs:
        return "identical on both sides"
    if same_as_ours:
        return f"kept the {conflicts.ours} version"
    if same_as_theirs:
        return f"took the {conflicts.theirs} version"
    return (
        f"combined both sides ({_numstat(workspace, f'{merge_sha}^1', merge_sha, path)} "
        f"against {conflicts.ours}, {_numstat(workspace, f'{merge_sha}^2', merge_sha, path)} "
        f"against {conflicts.theirs})"
    )


def _same_in(workspace: str, base: str, target: str, path: str) -> bool:
    return _run_git(workspace, "diff", "--quiet", base, target, "--", path).returncode == 0


def _numstat(workspace: str, base: str, target: str, path: str) -> str:
    result = _run_git(workspace, "diff", "--numstat", base, target, "--", path)
    fields = result.stdout.split()
    if len(fields) < 2:
        return "changed"
    return f"+{fields[0]} -{fields[1]}"


def merge_committed_by_resolver(workspace: str, conflicts: MergeConflicts, merged_sha: str) -> bool:
    """True when HEAD is a merge of the snapshot's tip and *merged_sha* on the original branch.

    A resolver that abandons the merge and checks out the incoming branch (or
    any other branch containing *merged_sha*) does not pass: the original tip
    must be a parent of HEAD and the branch must be the one the merge started on.
    """
    if conflicts.ours and conflicts.ours != "HEAD" and current_branch(workspace) != conflicts.ours:
        return False
    parents = set(commit_parents(workspace, head_sha(workspace)))
    return bool(conflicts.head) and conflicts.head in parents and merged_sha in parents


def conflict_resolution_task(
    conflicts: MergeConflicts,
    *,
    merged_ref: str,
    context_lines: Sequence[str] = (),
) -> str:
    """Instructions for a coding agent to resolve *conflicts* in the working tree."""
    lockfiles = [c.path for c in conflicts.paths if c.path.rsplit("/", 1)[-1] in LOCKFILE_NAMES]
    lines = [
        f"Resolve the merge conflicts from merging {conflicts.theirs} into {conflicts.ours}.",
        "",
        *context_lines,
        f"The merge of {merged_ref} is in progress in the workspace; "
        "do not abort, reset, or commit it.",
        "",
        "Conflicted files:",
        *(f"- {c.path}: {c.description}" for c in conflicts.paths),
        "",
        f"Keep both the intent of {conflicts.ours} and every change from "
        f"{conflicts.theirs}; remove all conflict markers.",
        "Resolve every hunk. Leave conflict markers only where the two sides make "
        "contradictory decisions that a person must choose between, and name that "
        "decision in your summary. Never leave markers on formatting, comments, "
        f"versions, dates, or bookkeeping metadata: take the {conflicts.theirs} side there.",
    ]
    if lockfiles:
        lines.append(
            "Do not hand-edit lockfiles "
            f"({', '.join(lockfiles)}): take the {conflicts.theirs} side, then regenerate "
            "them from the resolved manifest with the project's package manager "
            "(for example `pnpm install --lockfile-only`, `npm install --package-lock-only`, "
            "`uv lock`, `poetry lock --no-update`, `cargo generate-lockfile`)."
        )
    lines.extend(
        [
            "Leave the resolved files in the working tree; OpenSRE stages and commits the merge.",
            "Finish with a concise summary of how each conflict was resolved.",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "LOCKFILE_NAMES",
    "ConflictHunk",
    "HunkComparison",
    "MergeConflicts",
    "ResolvedPath",
    "compare_hunks",
    "conclude_merge",
    "conflict_resolution_task",
    "describe_resolutions",
    "merge_committed_by_resolver",
    "merge_conflicts",
    "parse_conflict_hunks",
    "unresolved_conflicts",
]
