"""Action tool: dispatch a natural-language "fix this Sentry issue" request.

This is the intake-routing half of the Sentry issue-fix flow. It exposes the
``fix_sentry_issue`` capability to the interactive-shell **action agent** as a
proper AgentTool, so a request like *"fix this Sentry issue <url> and open a
PR"* is dispatched by the action LLM (semantic intent) rather than by any
regex/keyword shortcut. The tool is only offered when the fix capability is
opted in (``PI_ISSUE_FIX_ENABLED``); the PR-shipping gate + token check stay
inside ``fix_sentry_issue`` itself.
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.tools import ActionToolScope, execute_with_action_context
from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool, SideEffectLevel
from core.tool_framework.utils import object_schema, string_property
from tools.cross_vendor.fix_sentry_issue import fix_sentry_issue
from tools.cross_vendor.fix_sentry_issue.runner import is_issue_fix_enabled
from tools.interactive_shell.shared import allow_tool
from tools.interactive_shell.subprocess import require_subprocess_presenter


def _render_result(console: Any, out: dict[str, Any]) -> None:
    """Print a concise, human-readable summary of the tool result."""
    if not out.get("success"):
        console.print(
            f"[red]Could not fix the Sentry issue[/] "
            f"({out.get('error_kind') or 'error'}): {out.get('error') or ''}"
        )
        # Point the user at their fix so they can recover manually, with guidance
        # matched to how far shipping got on the new branch:
        #   commit_failed -> on the branch but NOT committed (edits staged only)
        #   push_failed   -> committed but not pushed
        #   pr_failed     -> pushed, only the PR call failed
        branch = out.get("branch_name")
        if branch:
            kind = out.get("error_kind")
            if kind == "commit_failed":
                console.print(
                    f"[dim]Your changes are on branch [cyan]{branch}[/] but not committed — "
                    "commit them, push, and open the PR manually.[/]"
                )
            elif kind == "pr_failed":
                console.print(
                    f"[dim]Your fix is pushed to branch [cyan]{branch}[/] — "
                    "open the PR manually.[/]"
                )
            else:
                console.print(
                    f"[dim]Your fix is committed on branch [cyan]{branch}[/] — "
                    "push it and open the PR manually.[/]"
                )
        elif out.get("changed_files"):
            console.print("[dim]The proposed fix is still in your working tree.[/]")
        return

    console.print(f"[green]Fixed Sentry issue {out.get('issue_id')}[/].")
    if out.get("summary"):
        console.print(str(out["summary"]))
    for path in out.get("changed_files") or []:
        console.print(f"  • {path}")
    if out.get("pr_url"):
        console.print(
            f"[bold]Opened pull request:[/] {out['pr_url']} "
            f"(branch [cyan]{out.get('branch_name')}[/])"
        )
    else:
        console.print("[dim]Diff left in your working tree (no PR requested).[/]")


def execute_sentry_fix_tool(args: dict[str, Any], ctx: ActionToolScope) -> bool:
    sentry_url = str(args.get("sentry_url", "")).strip()
    if not sentry_url:
        return False
    open_pr = bool(args.get("open_pr", False))
    presenter = require_subprocess_presenter(ctx)
    summary = f"fix Sentry issue {sentry_url}"
    if open_pr:
        summary += " and open a pull request"
    if not presenter.execution_allowed(
        allow_tool("sentry_issue_fix"),
        action_summary=summary,
    ):
        return True

    ctx.console.print(
        f"[bold]Fixing Sentry issue[/] {sentry_url}"
        + (" and opening a pull request…" if open_pr else "…")
    )
    out = fix_sentry_issue.run(sentry_url=sentry_url, open_pr=open_pr)
    _render_result(ctx.console, out)
    return True


def run_sentry_fix(*, sentry_url: str, context: Any, open_pr: bool = False) -> dict[str, Any]:
    return execute_with_action_context(
        {"sentry_url": sentry_url, "open_pr": open_pr},
        context,
        execute_sentry_fix_tool,
    )


fix_sentry_issue_start_tool = RegisteredTool(
    name="fix_sentry_issue_start",
    description=(
        "Fix a Sentry issue in code with a coding agent, given a Sentry issue URL. "
        "Use whenever the user asks to FIX, patch, resolve in code, or open/raise a pull "
        "request for a Sentry issue and provides a Sentry issue URL — e.g. 'fix this sentry "
        "issue <url>' or 'fix <url> and open a PR'. Set open_pr=true when they ask to "
        "open/create/raise a PR or to ship the fix; otherwise false to only produce a diff. "
        "Do NOT use for diagnose/analyze-only requests (answer with the read-only "
        "Sentry tools), for non-Sentry URLs, or when no Sentry issue URL is provided."
    ),
    input_schema=object_schema(
        properties={
            "sentry_url": string_property(
                description="The Sentry issue URL to fix (e.g. https://<org>.sentry.io/issues/<id>/).",
                min_length=1,
            ),
            "open_pr": {
                "type": "boolean",
                "description": (
                    "True when the user asks to open/create/raise a pull request or to ship "
                    "the fix; false to only produce a reviewable diff."
                ),
            },
        },
        required=("sentry_url",),
    ),
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    accepts_runtime_context=True,
    is_available=lambda _sources: is_issue_fix_enabled(),
    run=run_sentry_fix,
)


__all__ = [
    "execute_sentry_fix_tool",
    "fix_sentry_issue_start_tool",
    "run_sentry_fix",
]
