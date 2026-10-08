"""Sentry issue-fix tool: paste a Sentry issue URL and a coding agent proposes (and optionally ships) the fix.

Resolve the issue from Sentry, run the configured coding agent (via the neutral
``integrations/coding_agent`` seam; Pi today) in the **current workspace**, and
return a summary + git diff for review. When ``open_pr`` is requested (and the ship
gate is enabled), it then commits the fix to a fresh namespaced branch, pushes it,
and opens a GitHub pull request into the base branch. It **never** pushes to the
base/``main`` branch — the fix always lands on an ``opensre/sentry-fix-*`` branch and
is proposed via PR.

Package layout (separation of concerns):

- ``errors.py``    — :class:`FixIssueError` + stable ``error_kind`` constants.
- ``context.py``   — Sentry URL parse + issue fetch, compacted into a masked task.
- ``runner.py``    — opt-in gates, coding-agent readiness, the coding run, ship
  orchestration, and result shaping.
- ``ship.py``      — sequence branch -> commit -> push -> PR into a :class:`ShipResult`
  (git primitives live in ``integrations/git``; PR creation in ``integrations/github``).
- ``__init__.py``  — this file: the agent-facing :class:`BaseTool` contract. The
  class lives here because the tool registry discovers instances by
  ``__class__.__module__`` and does not recurse into sub-modules.

Gating: ``is_available`` is True only when ``PI_ISSUE_FIX_ENABLED`` is set (the fix
capability). Opening a PR requires a **second** opt-in, ``PI_ISSUE_FIX_SHIP_ENABLED``,
plus a GitHub token. Secrets (Sentry/GitHub tokens) never enter the coding-agent
prompt, the commit, the PR body, or the returned output — the issue context is masked.
"""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import BaseTool, SideEffectLevel
from tools.cross_vendor.fix_sentry_issue.context import gather_issue_context
from tools.cross_vendor.fix_sentry_issue.errors import FixIssueError
from tools.cross_vendor.fix_sentry_issue.runner import (
    SOURCE,
    ensure_cli_ready,
    ensure_enabled,
    ensure_ship_ready,
    error_output,
    is_issue_fix_enabled,
    pre_pi_changes,
    resolve_workspace,
    run_fix,
    run_ship,
    ship_error_output,
    to_output,
    with_ship_output,
)


class FixSentryIssueTool(BaseTool):
    """Resolve a Sentry issue and have a coding agent propose a fix (diff for review)."""

    name = "fix_sentry_issue"
    display_name = "Fix Sentry issue"
    source = SOURCE
    side_effect_level = SideEffectLevel.MUTATING
    surfaces = (ToolSurface.ACTION,)
    description = (
        "Given a Sentry issue URL, fetch the issue context and run a coding agent to "
        "propose a fix in the current repository, returning a summary plus the git diff. "
        "With open_pr=true (and PI_ISSUE_FIX_SHIP_ENABLED=1 plus a GitHub token) it commits "
        "the fix to a fresh branch, pushes it, and opens a pull request into the base branch "
        "— never pushing to main. Disabled unless PI_ISSUE_FIX_ENABLED=1, Sentry is "
        "configured, and a coding agent is installed."
    )
    use_cases = [
        "A user pastes a Sentry issue link and asks OpenSRE to fix it",
        "Turn a known Sentry error into a reviewable code change",
        "Fix a Sentry issue and open a pull request with the change",
    ]
    anti_examples = [
        "Investigating an issue without changing code (use the read-only Sentry tools)",
        "Merging the fix or pushing directly to main (only ever opens a PR)",
    ]
    input_schema = {
        "type": "object",
        "properties": {
            "sentry_url": {
                "type": "string",
                "description": "URL of the Sentry issue to fix (.../issues/<id>/).",
            },
            "workspace": {
                "type": "string",
                "description": (
                    "Absolute path to the repository to edit. "
                    "Defaults to PI_CODING_WORKSPACE or the current directory."
                ),
                "nullable": True,
            },
            "model": {
                "type": "string",
                "description": "Optional coding-agent model override (provider/model). "
                "Defaults to CODING_MODEL / PI_CODING_MODEL.",
                "nullable": True,
            },
            "open_pr": {
                "type": "boolean",
                "description": (
                    "When true, commit the fix to a fresh branch and open a GitHub PR "
                    "(requires PI_ISSUE_FIX_SHIP_ENABLED=1 and a GitHub token). Never pushes "
                    "to the base branch. Defaults to false (diff only)."
                ),
                "nullable": True,
            },
        },
        "required": ["sentry_url"],
    }
    outputs = {
        "success": "True when the coding agent produced a fix (and, if open_pr, the PR opened) cleanly",
        "error_kind": "Stable failure category (disabled, invalid_input, sentry_unavailable, "
        "issue_not_found, cli_unavailable, timeout, execution_error, ship_disabled, "
        "github_token_missing, not_a_git_repo, no_changes, protected_branch, branch_failed, "
        "commit_failed, push_failed, pr_failed) or None on success",
        "issue_id": "The resolved Sentry issue id",
        "summary": "the coding agent's summary of the fix",
        "changed_files": "Files modified in the working tree",
        "diff": "git diff of the proposed fix (truncated if large)",
        "branch_name": "Branch the fix was committed to (when open_pr), else None",
        "pr_url": "URL of the opened pull request (when open_pr), else None",
        "pr_number": "Number of the opened pull request (when open_pr), else None",
        "error": "Human-readable error detail when the run failed",
    }

    def is_available(self, _sources: dict[str, dict]) -> bool:
        """Only available when explicitly opted in (cheap flag check)."""
        return is_issue_fix_enabled()

    def run(
        self,
        sentry_url: str,
        workspace: str | None = None,
        model: str | None = None,
        open_pr: bool = False,
    ) -> dict[str, Any]:
        ws = resolve_workspace(workspace)
        try:
            ensure_enabled()
            ctx = gather_issue_context(sentry_url)
        except FixIssueError as exc:
            # The issue id isn't resolved yet, so it stays empty here.
            return error_output(exc.kind, exc.message)

        try:
            ensure_cli_ready()
            if open_pr:
                # Fail fast before spending a Pi run if a PR could never be opened.
                ensure_ship_ready(ws)
        except FixIssueError as exc:
            # The issue is resolved; keep its id in the error output.
            return error_output(exc.kind, exc.message, ctx.issue_id)

        # Fingerprint what's already dirty before Pi runs, so shipping commits only
        # what Pi adds or changes — never developer work-in-progress it leaves alone.
        baseline = pre_pi_changes(ws) if open_pr else {}

        # Unexpected exceptions propagate to BaseTool.__call__ (Sentry-reported).
        result = run_fix(ctx, ws, model)
        output = to_output(ctx.issue_id, result)
        if not (open_pr and result.success):
            return output

        try:
            ship = run_ship(ctx.issue_id, sentry_url, result, ws, baseline=baseline)
        except FixIssueError as exc:
            # The fix is in the working tree; report why shipping failed but keep the diff.
            return ship_error_output(output, exc)
        return with_ship_output(output, ship)


# Module-level instance so the tool registry auto-discovers it (see tools/registry.py).
fix_sentry_issue = FixSentryIssueTool()
