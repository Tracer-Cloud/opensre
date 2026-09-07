"""Read-only tool: CI reliability KPIs and developer blocked time for one repository."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any

from rich.markup import escape

from core.agent_harness.tools import action_context_from_agent_context
from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel, report_run_error
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.github.client import GitHubApiError, GitHubRestClient, resolve_github_token
from integrations.github.helpers import (
    GITHUB_INJECTED_PARAMS,
    github_creds,
    github_source_available,
)
from integrations.github.repo_scope import detect_git_remote_repo_scope
from integrations.github.tools.ci_analytics.collector import collect_runs
from integrations.github.tools.ci_analytics.metrics import compute_report
from integrations.github.tools.ci_analytics.models import CiAnalyticsReport, FailureKind
from integrations.github.tools.ci_analytics.render import (
    format_minutes,
    headline,
    render_markdown,
    render_report,
)

TOOL_NAME = "analyze_github_ci_reliability"
_SOURCE = "github"
_DEFAULT_WINDOW_DAYS = 30
_MIN_WINDOW_DAYS = 1
_MAX_WINDOW_DAYS = 90


def _available(sources: dict[str, dict]) -> bool:
    gh = sources.get("github", {})
    return bool(
        github_source_available(sources)
        or resolve_github_token(None)
        or github_creds(gh).get("github_token")
    )


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    gh = sources.get("github", {})
    if not gh:
        return {}
    params = github_creds(gh)
    for key in ("owner", "repo"):
        value = str(gh.get(key) or "").strip()
        if value:
            params[key] = value
    return params


def _console(context: Any) -> Any:
    if context is None:
        return None
    try:
        return action_context_from_agent_context(context).console
    except RuntimeError:
        return None


def _failure_message(exc: Exception, *, repository: str) -> str:
    """User-facing failure text by status class; exception detail stays in Sentry only."""
    status = getattr(exc, "status_code", None)
    if status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
        return (
            f"GitHub rejected the token for {repository}; it needs read access to Actions and "
            "pull requests. Run `opensre integrations setup github` and try again."
        )
    if status == HTTPStatus.NOT_FOUND:
        return f"GitHub repository {repository} was not found or is not accessible with this token."
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        return f"GitHub rate limit reached while reading {repository}; try again in a few minutes."
    if isinstance(exc, ValueError):
        return f"GitHub returned an unexpected payload for {repository}; the report was not built."
    return f"Could not read the GitHub Actions history of {repository} ({type(exc).__name__})."


def _map_evidence(evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]) -> None:
    if output.get("success"):
        record_evidence_entry(
            evidence,
            source=TOOL_NAME,
            label="GitHub CI reliability",
            summary=str(output.get("summary") or ""),
        )


def _payload(report: CiAnalyticsReport) -> dict[str, Any]:
    return {
        "executions": report.executions,
        "pr_executions": report.pr_executions,
        "pr_failures": report.pr_failures,
        "pr_failure_rate": report.pr_failure_rate,
        "reliability_failures": report.count(FailureKind.RELIABILITY),
        "source_failures": report.count(FailureKind.SOURCE),
        "unresolved_failures": report.count(FailureKind.UNRESOLVED),
        "blocked_minutes": round(report.blocked_minutes, 1),
        "blocked_minutes_all": round(report.blocked_minutes_all, 1),
        "merged_pr_branches": report.merged_pr_branches,
        "branch_runs": report.branch_runs,
        "branch_failures": report.branch_failures,
        "red_hours": round(report.red_hours, 2),
        "outages": len(report.outages),
        "mean_recovery_hours": report.mean_recovery_hours,
        "workflows": [
            {
                "workflow": s.workflow,
                "runs": s.runs,
                "failures": s.failures,
                "reliability_failures": s.reliability_failures,
                "normal_minutes": s.normal_minutes,
            }
            for s in report.workflows
        ],
        "coverage_notices": list(report.coverage_notices),
    }


@tool(
    name=TOOL_NAME,
    source=_SOURCE,
    display_name="Analyze CI reliability",
    description=(
        "Read a repository's recent GitHub Actions history and report CI/CD "
        "reliability KPIs: executions, PR failure rate, failures classified as "
        "CI-caused (same commit passed later) versus source-code, developer time "
        "blocked by unreliable CI on merged PRs, and default-branch red time. "
        "Read-only; needs a GitHub token."
    ),
    use_cases=[
        "Analyze a repository's CI/CD performance and reliability",
        "How much developer time does flaky CI cost us",
        "How often does CI fail on pull requests in owner/repo",
        "How long was main broken last month",
    ],
    anti_examples=[
        "Fixing a failing check (use fix_github_pr_ci)",
        "Listing currently failing checks on open PRs (use the CI health report)",
        "Reading one workflow run's logs (use the GitHub Actions log tools)",
    ],
    requires=[],
    outputs={
        "executions": "Completed workflow runs counted in the window",
        "pr_failure_rate": "Failed share of PR-triggered runs",
        "reliability_failures": "Failures that passed later on the identical commit",
        "blocked_minutes": "Developer minutes blocked by CI-caused failures on merged PRs",
        "red_hours": "Hours the default branch had at least one red workflow",
        "headline": "One sentence naming the biggest cost, to repeat verbatim",
        "response_text": "The rendered report, or a one-line summary when the shell painted it",
    },
    surfaces=(ToolSurface.CHAT, ToolSurface.ACTION),
    side_effect_level=SideEffectLevel.READ_ONLY,
    parallel_safe=False,
    accepts_runtime_context=True,
    input_schema={
        "type": "object",
        "properties": {
            "owner": {
                "type": "string",
                "description": "Repository owner. Defaults to the current checkout's origin.",
            },
            "repo": {
                "type": "string",
                "description": "Repository name. Defaults to the current checkout's origin.",
            },
            "days": {
                "type": "integer",
                "minimum": _MIN_WINDOW_DAYS,
                "maximum": _MAX_WINDOW_DAYS,
                "description": f"Window in days, default {_DEFAULT_WINDOW_DAYS}.",
            },
            "workspace": {
                "type": "string",
                "description": "Local checkout used to detect owner/repo when not given.",
            },
            "github_token": {"type": "string"},
        },
        "additionalProperties": False,
    },
    is_available=_available,
    extract_params=_extract_params,
    injected_params=GITHUB_INJECTED_PARAMS,
    evidence_mapper=_map_evidence,
)
def analyze_github_ci_reliability(
    owner: str | None = None,
    repo: str | None = None,
    days: int | None = None,
    workspace: str | None = None,
    github_token: str | None = None,
    context: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Compute and render CI reliability KPIs for one repository window.

    In the interactive shell the report is painted straight to the console so
    every figure the user sees is the computed one; the returned
    ``response_text`` then only summarizes. Other surfaces get the markdown.
    """
    window = min(max(int(days or _DEFAULT_WINDOW_DAYS), _MIN_WINDOW_DAYS), _MAX_WINDOW_DAYS)
    repo_owner = (owner or "").strip()
    repo_name = (repo or "").strip().removesuffix(".git")
    if not repo_owner or not repo_name:
        detected = detect_git_remote_repo_scope(workspace)
        if detected is not None:
            repo_owner, repo_name = detected
    if not repo_owner or not repo_name:
        return tool_unavailable(
            _SOURCE,
            "owner/repo is required unless the workspace origin identifies a GitHub repository.",
            response_text="I need a GitHub repository (owner/repo) to analyze.",
        )
    if not resolve_github_token(github_token):
        message = (
            f"A GitHub token is required to read the Actions history of {repo_owner}/{repo_name}. "
            "Run `opensre integrations setup github` and try again."
        )
        return tool_unavailable(_SOURCE, message, response_text=message)
    now = datetime.now(UTC)
    console = _console(context)
    if console is not None:
        console.print(
            f"[dim]Reading GitHub Actions history for {escape(f'{repo_owner}/{repo_name}')}, "
            f"last {window} days…[/dim]"
        )
    started = time.monotonic()
    try:
        collected = collect_runs(
            GitHubRestClient(github_token),
            owner=repo_owner,
            repo=repo_name,
            window_days=window,
            now=now,
        )
    except (GitHubApiError, ValueError) as exc:
        report_run_error(
            exc,
            tool_name=TOOL_NAME,
            source=_SOURCE,
            component="integrations.github.tools.ci_analytics.tool",
            method="collect_runs",
            extras={"owner": repo_owner, "repo": repo_name},
        )
        message = _failure_message(exc, repository=f"{repo_owner}/{repo_name}")
        return tool_unavailable(_SOURCE, message, response_text=message)
    report = compute_report(
        owner=repo_owner,
        repo=repo_name,
        default_branch=collected.default_branch,
        window_days=window,
        branch_runs=collected.branch_runs,
        pr_runs=collected.pr_runs,
        merged_prs=collected.merged_prs,
        now=now,
        coverage_notices=collected.coverage_notices,
    )
    summary = (
        f"{repo_owner}/{repo_name}: {report.executions} runs in {window} days, "
        f"{report.pr_failures} of {report.pr_executions} PR runs failed, "
        f"{report.count(FailureKind.RELIABILITY)} CI-caused, "
        f"{format_minutes(report.blocked_minutes)} of developer time blocked on merged PRs."
    )
    rendered = console is not None
    if console is not None:
        read = len(collected.branch_runs) + len(collected.pr_runs)
        console.print(f"[dim]Read {read} runs in {time.monotonic() - started:.0f}s.[/dim]")
        console.print()
    base = {
        "source": _SOURCE,
        "success": True,
        "owner": repo_owner,
        "repo": repo_name,
        "default_branch": collected.default_branch,
        "window_days": window,
        "summary": summary,
        "headline": headline(report),
        "rendered_in_shell": rendered,
    }
    if rendered:
        # The shell already shows every figure; handing the raw numbers back
        # as well only invites the model to retype them, so they stay out.
        render_report(console, report)
        return {
            **base,
            "coverage_notices": list(report.coverage_notices),
            "response_text": summary,
        }
    return {**base, **_payload(report), "response_text": render_markdown(report)}


__all__ = ["TOOL_NAME", "analyze_github_ci_reliability"]
