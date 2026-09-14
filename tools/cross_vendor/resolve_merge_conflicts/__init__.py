"""Merge-conflict resolution tool: a coding agent resolves the conflicted files, OpenSRE commits the merge.

Package layout:

- ``errors.py``  — :class:`ResolveMergeError` + stable ``error_kind`` constants.
- ``runner.py``  — the lifecycle: snapshot the stopped merge, run the coding agent
  (via the neutral ``integrations/coding_agent`` seam), verify, stage, commit.
- ``__init__.py`` — the agent-facing :class:`BaseTool` contract. The class lives
  here because the tool registry discovers instances by ``__class__.__module__``.

The tool never aborts a merge: files the agent cannot settle are reported with
the merge left in progress, so the user decides them. Committing the merge and
pushing the branch go through the shell's ``/auto`` policy (the ``merge_push``
tool type): High proceeds, lower levels ask first, an unattended run proceeds.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.agent_harness.tools import ActionToolScope, action_context_from_agent_context
from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from tools.cross_vendor.resolve_merge_conflicts.runner import SOURCE, resolve_merge
from tools.interactive_shell.shared import allow_tool

_MERGE_PUSH_TOOL_TYPE = "merge_push"


def _action_scope(context: Any) -> ActionToolScope | None:
    if context is None:
        return None
    try:
        return action_context_from_agent_context(context)
    except RuntimeError:
        return None


def _approval(scope: ActionToolScope | None) -> Callable[[str], bool] | None:
    """Ask the shell's execution policy (``/auto`` level) to allow the commit and push."""
    presenter = getattr(scope, "subprocess_presenter", None)
    if presenter is None:
        return None

    def approve(action: str) -> bool:
        return bool(
            presenter.execution_allowed(allow_tool(_MERGE_PUSH_TOOL_TYPE), action_summary=action)
        )

    return approve


class ResolveMergeConflictsTool(BaseTool):
    """Resolve the conflicts of a git merge with a coding agent and commit the merge."""

    name = "resolve_merge_conflicts"
    display_name = "Resolve merge conflicts"
    source = SOURCE
    side_effect_level = SideEffectLevel.MUTATING
    surfaces = (ToolSurface.ACTION,)
    requires_approval = True
    accepts_runtime_context = True
    approval_reason = (
        "Runs a coding agent that edits the conflicted files in the repository, "
        "then commits the merge and pushes the branch."
    )
    description = (
        "Resolve the git merge conflicts in the current repository with a coding agent, "
        "show each conflict side by side, then commit the merge and push the branch to "
        "update its pull request. Use whenever a merge stopped on conflicts (git reported "
        "'CONFLICT', 'Unmerged paths', or files hold '<<<<<<<' markers) or the user asks "
        "to resolve, fix, or finish a merge, or to commit and push a resolved merge. It "
        "works on the merge already in progress, or merges the named branch first. Before "
        "committing it checks that no conflict marker or unmerged path remains; files it "
        "cannot settle are reported and the merge is left in progress for the user to "
        "decide. The commit and push follow the shell's /auto approval level."
    )
    use_cases = [
        "Resolve the merge conflicts in the current repository and commit the merge",
        "Finish a merge of main into the feature branch that stopped on conflicts",
        "Merge a branch into the current branch and resolve any conflicts",
        "Resolve the remaining conflicted files following the user's decision",
        "Commit and push a merge whose conflicts were already resolved in the working tree",
    ]
    anti_examples = [
        "Implementing a code change that is not a merge conflict (use code_implement)",
        "Conflicts of a rebase or cherry-pick (only git merge is supported)",
        "Opening a pull request (it only updates the branch the merge is on)",
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
        "outcome": "One sentence stating whether OpenSRE committed and pushed the merge; "
        "repeat it to the user",
        "success": "True when the merge was committed with every conflict resolved",
        "error_kind": "Stable failure category (no_merge_in_progress, not_a_git_repo, "
        "merge_failed, cli_unavailable, timeout, execution_error, conflicts_remain, "
        "merge_abandoned, commit_failed) or None on success",
        "error": "Human-readable failure detail",
        "workspace": "Repository the merge ran in",
        "branch": "Branch that received the merge",
        "merged": "Branch or commit that was merged in",
        "commit_sha": "The merge commit, or None when the merge was not committed",
        "pushed": "True when the branch was pushed after the commit",
        "pushed_to": "remote/branch the merge commit was pushed to, else empty",
        "resolved_files": "Conflicted files the coding agent resolved",
        "unresolved_files": "Conflicted files still waiting for a decision",
        "coding_agent_summary": "The coding agent's account of how it resolved each file, "
        "written before OpenSRE committed the merge",
        "merge_in_progress": "True when the merge is still open in the working tree",
        "rendered_in_shell": "True when each conflict hunk was already shown side by side "
        "(ours, theirs, merged) in the terminal",
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
        context: Any = None,
    ) -> dict[str, Any]:
        scope = _action_scope(context)
        return resolve_merge(
            workspace,
            ref=ref,
            model=model,
            instructions=instructions,
            console=getattr(scope, "console", None),
            approve=_approval(scope),
        )


# Module-level instance so the tool registry auto-discovers it (see tools/registry.py).
resolve_merge_conflicts = ResolveMergeConflictsTool()

__all__ = ["ResolveMergeConflictsTool", "resolve_merge_conflicts"]
