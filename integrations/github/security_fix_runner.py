"""Scheduled runner for the ``fixing-github-security-alerts`` recurring skill.

Each tick fixes at most one open finding of the configured repository and
opens a pull request for it, then renders the delivery text the scheduler
posts. The report is deterministic: no model turn stands between the fix
result and the channel.
"""

from __future__ import annotations

from typing import Any

from infrastructure.scheduling.scheduler.agent_runner import AgentPayload
from integrations.github.client import GitHubRestClient
from integrations.github.tools.security_fix.errors import (
    ERR_ALERT_NOT_FOUND,
    ERR_FIX_ALREADY_OPEN,
)
from integrations.github.tools.security_fix.unattended import run_unattended_security_fix

SECURITY_FIX_SKILL_NAME = "fixing-github-security-alerts"
_NOTHING_TO_FIX_KINDS = frozenset({ERR_ALERT_NOT_FOUND, ERR_FIX_ALREADY_OPEN})


def _required_text(payload: AgentPayload, key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise RuntimeError(f"GitHub security fix requires {key} in the scheduled skill inputs.")
    return value


def _finding_label(result: dict[str, Any]) -> str:
    alert_type = str(result.get("alert_type") or "").replace("_", " ").strip()
    number = result.get("alert_number")
    if not alert_type or number is None:
        return "finding"
    return f"{alert_type} finding #{number}"


def render_security_fix_report(owner: str, repo: str, result: dict[str, Any]) -> str:
    """Render one tick's outcome as the delivered report body."""
    heading = f"GitHub security fix — {owner}/{repo}"
    error_kind = result.get("error_kind")
    if result.get("success") and result.get("pr_url"):
        lines = [
            heading,
            f"Opened {result['pr_url']} for {_finding_label(result)}.",
        ]
        summary = str(result.get("alert_summary") or "").strip()
        if summary:
            lines.append(f"Finding: {summary}")
        changed = [str(path) for path in result.get("changed_files") or []]
        if changed:
            lines.append("Changed files: " + ", ".join(changed))
        lines.append("Review the pull request before merging.")
        return "\n".join(lines)
    detail = " ".join(str(result.get("error") or "").split()) or "No automatic fix was produced."
    if error_kind in _NOTHING_TO_FIX_KINDS:
        return "\n".join((heading, f"Nothing to fix this run: {detail}"))
    label = _finding_label(result)
    alert_url = str(result.get("alert_url") or "").strip()
    target = f"{label} ({alert_url})" if alert_url else label
    return "\n".join((heading, f"No pull request opened for {target}: {detail}"))


def run_github_security_fix(
    payload: AgentPayload,
    *,
    client: GitHubRestClient | None = None,
) -> str:
    """Fix one finding of the scheduled repository, open a PR, and return the report."""
    owner = _required_text(payload, "owner")
    repo = _required_text(payload, "repo")
    workspace = str(payload.get("workspace") or "").strip() or None
    result = run_unattended_security_fix(
        owner=owner,
        repo=repo,
        alert_type="auto",
        workspace=workspace,
        client=client,
    )
    return render_security_fix_report(owner, repo, result)


__all__ = [
    "SECURITY_FIX_SKILL_NAME",
    "render_security_fix_report",
    "run_github_security_fix",
]
