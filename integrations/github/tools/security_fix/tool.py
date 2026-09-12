"""Action tool for fixing GitHub security or quality findings and optionally opening a PR."""

from __future__ import annotations

from typing import Any

from core.agent_harness.tools import action_context_from_agent_context
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.github.client import resolve_github_token
from integrations.github.helpers import (
    GITHUB_INJECTED_PARAMS,
    github_creds,
    github_source_available,
)
from integrations.github.tools.security_fix.runner import run_security_fix

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "owner": {
            "type": "string",
            "description": "GitHub repository owner. Optional when alert_url or workspace origin provides it.",
        },
        "repo": {
            "type": "string",
            "description": "GitHub repository name. Optional when alert_url or workspace origin provides it.",
        },
        "alert_type": {
            "type": "string",
            "enum": [
                "auto",
                "dependabot",
                "code_scanning",
                "code_quality",
                "secret_scanning",
            ],
            "description": "Finding family. auto selects the highest-severity open supported alert or code-quality finding.",
        },
        "alert_number": {
            "type": "integer",
            "description": "GitHub alert number. Optional; omitted means select an open supported alert.",
        },
        "alert_url": {
            "type": "string",
            "description": "Optional GitHub security alert URL.",
        },
        "workspace": {
            "type": "string",
            "description": "Absolute path to the local checkout to edit. Defaults to CODING_WORKSPACE or cwd.",
        },
        "model": {
            "type": "string",
            "description": "Optional coding-agent model override.",
        },
        "open_pr": {
            "type": "boolean",
            "description": "True when the user asks to raise/open a pull request or ship the fix.",
        },
        "github_token": {
            "type": "string",
            "description": "GitHub token injected from the configured integration.",
        },
    },
    "additionalProperties": False,
}


def _github_security_fix_available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(
        github_source_available(sources)
        or resolve_github_token(None)
        or github_creds(gh).get("github_token")
    )


def _github_security_fix_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    if not gh:
        return {}
    params = github_creds(gh)
    owner = str(gh.get("owner") or "").strip()
    repo = str(gh.get("repo") or "").strip()
    if owner:
        params["owner"] = owner
    if repo:
        params["repo"] = repo
    return params


def _confirm_fn(context: Any) -> Any:
    if context is None:
        return None
    try:
        action_context = action_context_from_agent_context(context)
    except RuntimeError:
        return None
    return action_context.confirm_fn


@tool(
    name="fix_github_security_alert",
    source="github",
    display_name="Fix GitHub security or quality finding",
    description=(
        "Fix a GitHub security and quality issue, Dependabot alert, "
        "code-scanning/CodeQL alert, or Code Quality finding "
        "in the current repository with built-in local fixers and an auto-detected "
        "coding agent (Pi, Claude Code, Codex, or Cursor CLI). With open_pr=true, commit the "
        "fix to a fresh opensre/github-security-fix-* branch, push it, and open a PR. "
        "It refuses secret-scanning alerts because safe remediation requires "
        "credential rotation."
    ),
    use_cases=[
        "Fix a GitHub Dependabot alert and open a pull request",
        "Fix a GitHub code-scanning alert or code-scanning page backlog in the local checkout",
        "Fix a GitHub Code Quality finding from the Security and quality page",
        "Fix GitHub security and quality issues and raise a PR",
        "Auto-select one open supported security or quality finding from a repo and propose a PR",
    ],
    anti_examples=[
        "Creating or closing ordinary GitHub issues (use github_cli)",
        "Fixing secret-scanning alerts by sending secrets to an automated fixer",
        "Merging or closing the resulting PR",
    ],
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    requires_approval=True,
    approval_reason=("Edits files and can push a branch and open a GitHub PR."),
    accepts_runtime_context=True,
    input_schema=_INPUT_SCHEMA,
    is_available=_github_security_fix_available,
    extract_params=_github_security_fix_extract_params,
    injected_params=GITHUB_INJECTED_PARAMS,
)
def fix_github_security_alert(
    owner: str | None = None,
    repo: str | None = None,
    alert_type: str = "auto",
    alert_number: int | None = None,
    alert_url: str | None = None,
    workspace: str | None = None,
    model: str | None = None,
    open_pr: bool = False,
    github_token: str | None = None,
    context: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Run the GitHub security remediation flow."""
    return run_security_fix(
        owner=owner,
        repo=repo,
        alert_type=alert_type,
        alert_number=alert_number,
        alert_url=alert_url,
        workspace=workspace,
        model=model,
        open_pr=open_pr,
        github_token=github_token,
        confirm_fn=_confirm_fn(context),
    )


__all__ = ["fix_github_security_alert"]
