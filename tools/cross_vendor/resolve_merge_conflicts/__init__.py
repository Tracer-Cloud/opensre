"""Merge-conflict resolution tool: a coding agent resolves the conflicted files, OpenSRE commits the merge.

Package layout:

- ``errors.py``  — :class:`ResolveMergeError` + stable ``error_kind`` constants.
- ``runner.py``  — the lifecycle: snapshot the stopped merge, run the coding agent
  (via the neutral ``integrations/coding_agent`` seam), verify, stage, commit.
- ``__init__.py`` — the agent-facing :class:`BaseTool` contract. The class lives
  here because the tool registry discovers instances by ``__class__.__module__``.

The tool never aborts a merge and never pushes: files the agent cannot settle
are reported with the merge left in progress, so the user decides them.
"""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from tools.cross_vendor.resolve_merge_conflicts.runner import SOURCE, resolve_merge


class ResolveMergeConflictsTool(BaseTool):
    """Resolve the conflicts of a git merge with a coding agent and commit the merge."""

    name = "resolve_merge_conflicts"
    display_name = "Resolve merge conflicts"
    source = SOURCE
    side_effect_level = SideEffectLevel.MUTATING
    surfaces = (ToolSurface.ACTION,)
    requires_approval = True
    approval_reason = (
        "Runs a coding agent that edits the conflicted files in the repository, "
        "then commits the merge."
    )
    description = (
        "Resolve the git merge conflicts in the current repository with a coding agent "
        "and commit the merge. Use whenever a merge stopped on conflicts (git reported "
        "'CONFLICT', 'Unmerged paths', or files hold '<<<<<<<' markers) or the user asks "
        "to resolve, fix, or finish a merge. It works on the merge already in progress, "
        "or merges the named branch first. Before committing it checks that no conflict "
        "marker or unmerged path remains; files it cannot settle are reported and the "
        "merge is left in progress for the user to decide. Never pushes."
    )
    use_cases = [
        "Resolve the merge conflicts in the current repository and commit the merge",
        "Finish a merge of main into the feature branch that stopped on conflicts",
        "Merge a branch into the current branch and resolve any conflicts",
        "Resolve the remaining conflicted files following the user's decision",
    ]
    anti_examples = [
        "Implementing a code change that is not a merge conflict (use code_implement)",
        "Conflicts of a rebase or cherry-pick (only git merge is supported)",
        "Pushing the branch or opening a pull request",
    ]
    input_schema = {
        "type": "object",
        "properties": {
            "workspace": {
                "type": "string",
                "description": (
                    "Absolute path to the repository with the merge. "
                    "Defaults to CODING_WORKSPACE or the current directory."
                ),
                "nullable": True,
            },
            "ref": {
                "type": "string",
                "description": (
                    "Branch or commit to merge into the current branch when no merge is "
                    "in progress yet (for example 'main' or 'origin/main'). Omit to "
                    "resolve the merge already in progress."
                ),
                "nullable": True,
            },
            "instructions": {
                "type": "string",
                "description": (
                    "The user's decisions for specific files or hunks, passed to the coding "
                    "agent verbatim (for example 'keep our version of config.py')."
                ),
                "nullable": True,
            },
            "model": {
                "type": "string",
                "description": "Optional coding-agent model override. Defaults to CODING_MODEL.",
                "nullable": True,
            },
        },
    }
    outputs = {
        "success": "True when the merge was committed with every conflict resolved",
        "error_kind": "Stable failure category (no_merge_in_progress, not_a_git_repo, "
        "merge_failed, cli_unavailable, timeout, execution_error, conflicts_remain, "
        "merge_abandoned, commit_failed) or None on success",
        "error": "Human-readable failure detail",
        "workspace": "Repository the merge ran in",
        "branch": "Branch that received the merge",
        "merged": "Branch or commit that was merged in",
        "commit_sha": "The merge commit, or None when the merge was not committed",
        "resolved_files": "Conflicted files the coding agent resolved",
        "unresolved_files": "Conflicted files still waiting for a decision",
        "summary": "The coding agent's account of how each conflict was resolved",
        "merge_in_progress": "True when the merge is still open in the working tree",
    }

    def is_available(self, _sources: dict[str, dict]) -> bool:
        """Always offered; a missing coding agent is reported by the run itself."""
        return True

    def run(
        self,
        workspace: str | None = None,
        ref: str | None = None,
        instructions: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        return resolve_merge(workspace, ref=ref, model=model, instructions=instructions)


# Module-level instance so the tool registry auto-discovers it (see tools/registry.py).
resolve_merge_conflicts = ResolveMergeConflictsTool()

__all__ = ["ResolveMergeConflictsTool", "resolve_merge_conflicts"]
