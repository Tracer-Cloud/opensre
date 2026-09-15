"""The agent-facing contract of the merge-conflict resolution tool.

Listed in ``tools/cross_vendor/__init__.py`` (``TOOL_MODULES``) so the registry
scans this module, where the instance's class is defined.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from rich.markup import escape

from core.agent_harness.spi.handoff import AskUserQuestion, parse_ask_user_answers, question_key
from core.agent_harness.spi.session_state import (
    PendingUserChoice,
    session_terminal,
    set_auto_command,
)
from core.agent_harness.tools import ActionToolScope, action_context_from_agent_context
from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from integrations.git import GitCommandError, merge_in_progress, unmerged_paths
from integrations.github import checkout_pull_request
from tools.cross_vendor.resolve_merge_conflicts.runner import (
    ALL_FILES_OPTIONS,
    ALL_FILES_TITLE,
    SOURCE,
    FileChoice,
    failure_output,
    resolve_merge,
)
from tools.interactive_shell.shared import allow_tool

_MERGE_PUSH_TOOL_TYPE = "merge_push"
_CHOOSE_COMMAND = "/choose"
_MENU_HEADER = "Resolve merge conflicts"


def _menu_available(scope: ActionToolScope | None) -> bool:
    """True when the shell can open its selection menu after this turn."""
    if scope is None or scope.is_tty is False or session_terminal(scope.session) is None:
        return False
    ports = getattr(scope, "slash_ports", None)
    return ports is not None and bool(ports.tty_interactive())


def _ask(scope: ActionToolScope | None) -> Callable[[list[FileChoice]], bool] | None:
    """Queue the shell's per-file menu; the answers arrive with the next user message."""
    if not _menu_available(scope):
        return None
    assert scope is not None

    def ask(choices: list[FileChoice]) -> bool:
        if not choices:
            return False
        if len(choices) > 1 and not _all_files_answered(scope) and not _per_file_requested(scope):
            pending = PendingUserChoice(
                title=ALL_FILES_TITLE,
                options=ALL_FILES_OPTIONS,
                note=f"{len(choices)} files: {', '.join(c.path.rsplit('/', 1)[-1] for c in choices)}",
                custom_answer=False,
            )
        elif len(choices) == 1:
            only = choices[0]
            pending = PendingUserChoice(
                title=only.title,
                options=only.options,
                note="Free text is passed to the coding agent.",
            )
        else:
            pending = PendingUserChoice(
                title=_MENU_HEADER,
                options=choices[0].options,
                questions=tuple(
                    AskUserQuestion(
                        label=choice.path.rsplit("/", 1)[-1],
                        title=choice.title,
                        options=choice.options,
                    )
                    for choice in choices
                ),
            )
        scope.session.pending_user_choice = pending
        set_auto_command(scope.session, _CHOOSE_COMMAND)
        terminal = session_terminal(scope.session)
        if terminal is not None:
            terminal.awaiting_handoff_answer = True
        return True

    return ask


def _turn_answers(scope: ActionToolScope | None) -> dict[str, str]:
    if scope is None:
        return {}
    return {
        question_key(asked): answer
        for asked, answer in parse_ask_user_answers(getattr(scope, "turn_user_message", "") or "")
    }


def _per_file_requested(scope: ActionToolScope | None) -> bool:
    """True when the user asked to decide file by file in this turn's message."""
    text = (getattr(scope, "turn_user_message", "") or "").casefold()
    return "file by file" in text or "per file" in text


def _all_files_answered(scope: ActionToolScope | None) -> bool:
    """True once the user chose "decide file by file" for this merge."""
    answer = _turn_answers(scope).get(question_key(ALL_FILES_TITLE), "")
    return answer.casefold().startswith("decide file by file")


def _answered_decisions(scope: ActionToolScope | None, paths: list[str]) -> dict[str, str]:
    """Decisions the user made in the menu, read from this turn's message by question title.

    The all-files answer is returned under ``*``.
    """
    answers = _turn_answers(scope)
    decided: dict[str, str] = {}
    for_all = answers.get(question_key(ALL_FILES_TITLE))
    if for_all:
        decided["*"] = for_all
    for path in paths:
        answer = answers.get(question_key(f"Resolve {path}"))
        if answer:
            decided[path] = answer
    return decided


def _action_scope(context: Any) -> ActionToolScope | None:
    if context is None:
        return None
    try:
        return action_context_from_agent_context(context)
    except RuntimeError:
        return None


def _cancellation(scope: ActionToolScope | None) -> Callable[[], bool] | None:
    """Report whether the user pressed ESC, so no commit or push happens after that."""
    console = getattr(scope, "console", None)
    if console is None:
        return None
    return lambda: bool(getattr(console, "cancel_requested", False))


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
        "show each conflict side by side, then commit the merge, push the branch to "
        "update its pull request and wait for the pull request checks. Use whenever a merge stopped on conflicts (git reported "
        "'CONFLICT', 'Unmerged paths', or files hold '<<<<<<<' markers) or the user asks "
        "to resolve, fix, or finish a merge, or to commit and push a resolved merge. It "
        "works on the merge already in progress, or merges the named branch first. A pull "
        "request named by number or URL is cloned into a workspace of its own and merged "
        "there, so the current directory is never switched to another branch. Before "
        "committing it checks that no conflict marker or unmerged path remains; files it "
        "cannot settle are reported and the merge is left in progress for the user to "
        "decide. The commit and push follow the shell's /auto approval level."
    )
    use_cases = [
        "Resolve the merge conflicts in the current repository and commit the merge",
        "Resolve the merge conflicts of pull request #123 (or its URL) and update it",
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
            "pull_request": {
                "type": "string",
                "description": (
                    "The pull request to resolve, as a number, owner/repo#number or URL. Pass "
                    "it whenever the user names a pull request: the tool clones the "
                    "repository under OpenSRE's home, checks the pull request out there and "
                    "merges its base branch, leaving the current directory untouched. Never "
                    "check a pull request out in the current directory instead."
                ),
                "nullable": True,
            },
            "workspace": {
                "type": "string",
                "description": (
                    "Omit this: the merge is resolved in the current directory. Pass an "
                    "absolute path only when the user named a different checkout; never "
                    "derive one from a branch or remote name."
                ),
                "nullable": True,
            },
            "ref": {
                "type": "string",
                "description": (
                    "Branch or commit to merge into the current branch when no merge is in "
                    "progress yet. Omit it: the merge already in progress is resolved, and "
                    "otherwise the repository's default branch (origin/main) is merged. Pass "
                    "a ref only when the user named one."
                ),
                "nullable": True,
            },
            "decisions": {
                "type": "object",
                "description": (
                    "Per conflicted file, what the user decided: 'ours' (keep the current "
                    "branch), 'theirs' (take the merged branch), 'combine' (the coding agent "
                    "merges both), or free text the agent must follow. Files not listed are "
                    'resolved by the coding agent. Pass {"*": "each"} only when the '
                    "user asks to decide file by file; menu answers are read automatically."
                ),
                "additionalProperties": {"type": "string"},
                "nullable": True,
            },
            "instructions": {
                "type": "string",
                "description": (
                    "General guidance for the coding agent when it combines files "
                    "(for example 'bump the skill version to 2.1')."
                ),
                "nullable": True,
            },
            "model": {
                "type": "string",
                "description": "Optional coding-agent model override. Defaults to CODING_MODEL.",
                "nullable": True,
            },
            "wait_for_checks": {
                "type": "boolean",
                "description": (
                    "After the push, wait for the pull request's checks and report whether "
                    "they passed. Defaults to true."
                ),
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
        "workspace": "Repository the merge ran in (OpenSRE's own clone for a pull request)",
        "pull_request": "owner/repo#number when a pull request was cloned and merged",
        "branch": "Branch that received the merge",
        "merged": "Branch or commit that was merged in",
        "commit_sha": "The merge commit, or None when the merge was not committed",
        "pushed": "True when the branch was pushed after the commit",
        "pushed_to": "remote/branch the merge commit was pushed to, else empty",
        "pull_request_url": "The open pull request whose checks were watched, else empty",
        "checks_state": "passed, failed, timed_out, superseded, conflicted, or not_watched",
        "checks_detail": "One line on the checks outcome",
        "failing_checks": "Names of the checks that failed",
        "resolved_files": "Conflicted files the coding agent resolved",
        "unresolved_files": "Conflicted files still waiting for a decision",
        "menu": "'queued' when the per-file menu will open after this turn; end the turn",
        "instruction": "What to do when the menu is queued",
        "questions": "One question per unresolved file, with its options (keep ours, take "
        "theirs, combine) and the text of each side, for surfaces without a menu",
        "coding_agent_summary": "The coding agent's account of how it resolved each file, "
        "written before OpenSRE committed the merge",
        "merge_in_progress": "True when the merge is still open in the working tree",
        "up_to_date": "True when the branch already contained the merged ref: nothing was "
        "committed or pushed and nothing is left to do",
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
        decisions: dict[str, str] | None = None,
        wait_for_checks: bool | None = True,
        pull_request: str | None = None,
        context: Any = None,
    ) -> dict[str, Any]:
        scope = _action_scope(context)
        label = ""
        if pull_request:
            try:
                checkout = checkout_pull_request(pull_request, cwd=os.getcwd())
            except GitCommandError as exc:
                return {
                    **failure_output(workspace or os.getcwd(), exc.kind, exc.message),
                    "pull_request": pull_request,
                }
            workspace, label = checkout.workspace, checkout.label
            _say(scope, checkout.workspace, label, reused=checkout.reused)
        output = resolve_merge(
            workspace,
            ref=ref,
            model=model,
            instructions=instructions,
            decisions={**(decisions or {}), **_answered_decisions(scope, _open_paths(workspace))},
            console=getattr(scope, "console", None),
            approve=_approval(scope),
            wait_for_checks=wait_for_checks is not False,
            cancelled=_cancellation(scope),
            ask=_ask(scope),
        )
        return {**output, "pull_request": label}


def _say(scope: ActionToolScope | None, workspace: str, label: str, *, reused: bool) -> None:
    console = getattr(scope, "console", None)
    if console is None:
        return
    verb = "Continuing the merge of" if reused else "Checked out"
    console.print(f"[dim]  {verb} {escape(label)} in {escape(workspace)}[/]")


def _open_paths(workspace: str | None) -> list[str]:
    """Conflicted paths of the merge in progress, to match menu answers against."""
    ws = workspace or os.getcwd()
    try:
        return unmerged_paths(ws) if merge_in_progress(ws) else []
    except Exception:  # noqa: BLE001 - not a repository or git missing: nothing to match
        return []


# Module-level instance so the tool registry auto-discovers it (see tools/registry.py).
resolve_merge_conflicts = ResolveMergeConflictsTool()

__all__ = ["ResolveMergeConflictsTool", "resolve_merge_conflicts"]
