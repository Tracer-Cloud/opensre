"""Finish a merge that stopped on conflicts after a resolver edited the files.

Any caller that hands conflicted files to a coding agent uses the same three
steps: snapshot the stopped merge, check what the agent left unresolved, and
stage plus commit the merge. The caller decides what to do when files remain
unresolved (abort, or keep the merge in progress for a person).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from integrations.git.errors import MERGE_FAILED, GitCommandError
from integrations.git.local import changed_since_baseline, file_fingerprints
from integrations.git.merge import (
    ConflictedPath,
    commit_merge,
    describe_conflicts,
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
class MergeConflicts:
    """The unmerged paths of a stopped merge and their content at that moment."""

    ours: str
    theirs: str
    paths: tuple[ConflictedPath, ...]
    content: Mapping[str, str]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(conflict.path for conflict in self.paths)


def merge_conflicts(workspace: str, *, ours: str, theirs: str) -> MergeConflicts:
    """Snapshot the conflicts of the merge in progress in *workspace*."""
    conflicts = describe_conflicts(workspace, ours=ours, theirs=theirs)
    content = file_fingerprints(workspace, [conflict.path for conflict in conflicts])
    return MergeConflicts(ours=ours, theirs=theirs, paths=tuple(conflicts), content=content)


def unresolved_conflicts(workspace: str, conflicts: MergeConflicts) -> list[ConflictedPath]:
    """Conflicted paths the resolver left with markers or never touched.

    Delete/modify conflicts carry no markers, so an untouched file is judged by
    its content fingerprint being unchanged since the merge stopped.
    """
    marked = set(paths_with_conflict_markers(workspace, conflicts.names))
    current = file_fingerprints(workspace, conflicts.names)
    return [
        conflict
        for conflict in conflicts.paths
        if conflict.path in marked
        or current.get(conflict.path, "") == conflicts.content.get(conflict.path, "")
    ]


def conclude_merge(
    workspace: str, conflicts: MergeConflicts, *, baseline: Mapping[str, str]
) -> str:
    """Stage the resolver's edits plus the conflicted paths and commit the merge.

    *baseline* fingerprints the files that were already dirty before the
    resolver ran, so a person's unrelated work in progress is not swept into
    the merge commit. Raises ``GitCommandError`` when an unmerged path remains.
    """
    stage_paths(workspace, changed_since_baseline(workspace, baseline=baseline))
    stage_paths(workspace, conflicts.names)
    remaining = unmerged_paths(workspace)
    if remaining:
        raise GitCommandError(
            MERGE_FAILED,
            f"Conflicts remain in {', '.join(remaining)}; the merge was not committed.",
        )
    return commit_merge(workspace)


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
    "MergeConflicts",
    "conclude_merge",
    "conflict_resolution_task",
    "merge_conflicts",
    "unresolved_conflicts",
]
