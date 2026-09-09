"""Read-only tool: CI reliability KPIs and developer blocked time for one repository."""

from __future__ import annotations

import logging
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
from integrations.github.client import GitHubApiError, resolve_github_token
from integrations.github.helpers import (
    GITHUB_INJECTED_PARAMS,
    github_creds,
    github_source_available,
)
from integrations.github.repo_scope import detect_git_remote_repo_scope
from integrations.github.tools.ci_analytics.analysis import analyze_repository
from integrations.github.tools.ci_analytics.models import CiAnalyticsReport, FailureKind
from integrations.github.tools.ci_analytics.render import (
    comparison_markdown,
    format_minutes,
    headline,
    key_results_payload,
    render_comparison,
    render_markdown,
    render_report,
)
from integrations.github.tools.ci_analytics.snapshots import (
    read_fresh_snapshot,
    report_from_dict,
    report_to_dict,
    snapshot_root,
    write_snapshot,
)

TOOL_NAME = "analyze_github_ci_reliability"
logger = logging.getLogger(__name__)

_SOURCE = "github"
_DEFAULT_WINDOW_DAYS = 30
_MIN_WINDOW_DAYS = 1
_MAX_WINDOW_DAYS = 90
_DEFAULT_BENCHMARKS = (("apache", "airflow"), ("fastapi", "fastapi"))


def _flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


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
    """The terminal to paint on, or None when the caller only reads the result.

    A headless run (scheduled loop, gateway) carries a capture console; painting
    there would hide the report, so it is returned as text instead.
    """
    if context is None:
        return None
    try:
        console = action_context_from_agent_context(context).console
    except RuntimeError:
        return None
    return console if getattr(console, "is_terminal", False) else None


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


def report_payload(report: CiAnalyticsReport) -> dict[str, Any]:
    """The report's figures as plain JSON-ready values."""
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
        "blocked_working_minutes": round(report.blocked_working_minutes, 1),
        "working_hours": report.working_hours_label,
        "developers_affected": report.developers_affected,
        "developers": [
            {
                "login": w.login,
                "pull_requests": w.pull_requests,
                "working_minutes": round(w.working_minutes, 1),
                "working_minutes_per_week": round(w.working_minutes_per_week, 1),
            }
            for w in report.developer_waits[:10]
        ],
        "blocked_prs": [
            {
                "pr_number": d.pr_number,
                "author": d.author,
                "branch": d.branch,
                "delay_minutes": round(d.delay_minutes, 1),
                "working_minutes": round(d.working_minutes, 1),
                "commits": d.commits,
            }
            for d in report.blocked_pr_delays[:10]
        ],
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


def _from_snapshot(
    snapshot: dict[str, Any],
    owner: str,
    repo: str,
    window: int,
    console: Any,
    *,
    include_benchmarks: bool = False,
    token: str = "",
) -> dict[str, Any]:
    """Answer from a same-day snapshot with the same renderer as a live analysis."""
    generated = str(snapshot.get("generated_at", ""))[:16].replace("T", " ")
    saved = snapshot.get("report")
    report = report_from_dict(saved) if isinstance(saved, dict) else None
    if console is not None:
        console.print(
            f"  [dim]Using the CI reliability snapshot of {escape(f'{owner}/{repo}')} "
            f"from {generated} UTC (same {window}-day window).[/dim]"
        )
        console.print()
    if report is not None:
        result = _result(
            report,
            owner,
            repo,
            window,
            console,
            include_benchmarks=include_benchmarks,
            token=token,
        )
        result["summary"] += f" Figures as of {generated} UTC, from the saved snapshot."
        result["from_snapshot"] = snapshot.get("generated_at")
        if console is not None:
            result["response_text"] = result["summary"]
        return result
    # An older snapshot without the report object: the figures only.
    figures = {
        key: value
        for key, value in snapshot.items()
        if key not in {"generated_at", "headline", "snapshot_path", "window_days", "markdown"}
    }
    summary = (
        f"{owner}/{repo}: {snapshot.get('executions')} runs in {window} days, "
        f"{snapshot.get('pr_failures')} of {snapshot.get('pr_executions')} PR runs failed, "
        f"{snapshot.get('reliability_failures')} CI-caused. "
        f"Figures as of {generated} UTC, from the saved snapshot."
    )
    return {
        "source": _SOURCE,
        "success": True,
        "owner": owner,
        "repo": repo,
        "window_days": window,
        "summary": summary,
        "headline": str(snapshot.get("headline", "")),
        "from_snapshot": snapshot.get("generated_at"),
        "rendered_in_shell": False,
        **figures,
        "response_text": summary,
    }


def _peer_payload(report: CiAnalyticsReport, *, from_snapshot: str | None) -> dict[str, Any]:
    return {
        "owner": report.owner,
        "repo": report.repo,
        "from_snapshot": from_snapshot,
        "key_results": key_results_payload(report),
        "red_hours": round(report.red_hours, 2),
        "mean_recovery_hours": report.mean_recovery_hours,
        "pr_failure_rate": report.pr_failure_rate,
        "reliability_failures": report.count(FailureKind.RELIABILITY),
        "pr_executions": report.pr_executions,
    }


def _load_peer_report(
    owner: str,
    repo: str,
    *,
    window: int,
    token: str,
    now: datetime,
    console: Any,
) -> tuple[CiAnalyticsReport, str | None] | None:
    """A benchmark report from today's snapshot, or a live read. ``None`` on failure."""
    snapshot = read_fresh_snapshot(snapshot_root(), owner, repo, window_days=window, now=now)
    if snapshot is not None:
        saved = snapshot.get("report")
        if isinstance(saved, dict):
            report = report_from_dict(saved)
            return report, str(snapshot.get("generated_at") or "")
    if console is not None:
        console.print(
            f"  [dim]Reading GitHub Actions history for {escape(f'{owner}/{repo}')} "
            f"(benchmark), last {window} days…[/dim]"
        )
    try:
        analysis = analyze_repository(owner, repo, token=token, days=window, now=now)
    except (GitHubApiError, ValueError):
        logger.warning("Benchmark analysis failed for %s/%s", owner, repo, exc_info=True)
        return None
    try:
        write_snapshot(
            snapshot_root(),
            owner,
            repo,
            now,
            {
                "generated_at": now.isoformat(),
                "window_days": window,
                "headline": headline(analysis.report),
                "report": report_to_dict(analysis.report),
                **report_payload(analysis.report),
            },
        )
    except OSError:
        logger.warning("Could not save the CI reliability snapshot", exc_info=True)
    return analysis.report, None


def _attach_benchmarks(
    result: dict[str, Any],
    report: CiAnalyticsReport,
    *,
    window: int,
    token: str,
    console: Any,
) -> dict[str, Any]:
    """Add the host comparison table and structured benchmark rows."""
    now = datetime.now(UTC)
    peers: list[CiAnalyticsReport] = []
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    for owner, repo in _DEFAULT_BENCHMARKS:
        if owner == report.owner and repo == report.repo:
            continue
        loaded = _load_peer_report(
            owner, repo, window=window, token=token, now=now, console=console
        )
        if loaded is None:
            skipped.append(f"{owner}/{repo}")
            continue
        peer, stamp = loaded
        peers.append(peer)
        rows.append(_peer_payload(peer, from_snapshot=stamp))
    result["benchmarks"] = rows
    if skipped:
        result["benchmarks_skipped"] = skipped
    if not peers:
        return result
    if console is not None:
        render_comparison(console, report, peers)
        return result
    compare = comparison_markdown(report, peers)
    result["comparison_text"] = compare
    result["response_text"] = f"{result['response_text']}\n\n{compare}"
    return result


def _result(
    report: CiAnalyticsReport,
    owner: str,
    repo: str,
    window: int,
    console: Any,
    *,
    include_benchmarks: bool = False,
    token: str = "",
) -> dict[str, Any]:
    """The tool's return for ``report``: painted in the shell, markdown elsewhere."""
    summary = (
        f"{owner}/{repo}: {report.executions} runs in {window} days, "
        f"{report.pr_failures} of {report.pr_executions} PR runs failed, "
        f"{report.count(FailureKind.RELIABILITY)} CI-caused, "
        f"{format_minutes(report.blocked_working_minutes)} of developer downtime "
        f"({format_minutes(report.blocked_minutes)} wall clock) on merged PRs."
    )
    takeaways = key_results_payload(report)
    base = {
        "source": _SOURCE,
        "success": True,
        "owner": owner,
        "repo": repo,
        "default_branch": report.default_branch,
        "window_days": window,
        "summary": summary,
        "headline": headline(report),
        "key_results": takeaways,
        "rendered_in_shell": console is not None,
    }
    if console is not None:
        render_report(console, report, compact=include_benchmarks)
        result = {
            **base,
            "coverage_notices": list(report.coverage_notices),
            "response_text": summary,
        }
    else:
        result = {**base, **report_payload(report), "response_text": render_markdown(report)}
    if include_benchmarks and token:
        result = _attach_benchmarks(result, report, window=window, token=token, console=console)
    return result


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
        "blocked_minutes": "Wall-clock minutes merged PRs waited past their expected green time",
        "blocked_working_minutes": "The part of that wait inside working hours: developer downtime",
        "red_hours": "Hours the default branch had at least one red workflow",
        "headline": "One sentence naming the biggest cost (already painted; do not repeat)",
        "key_results": "The five takeaway rows, red time first, even when the shell painted the report",
        "response_text": "The rendered report, or a one-line summary when the shell painted it",
        "benchmarks": "When include_benchmarks is true: Airflow and FastAPI rows from the same window",
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
            "include_benchmarks": {
                "type": "boolean",
                "description": (
                    "Also compare this repository with apache/airflow and fastapi/fastapi "
                    "over the same window. Default false."
                ),
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
    include_benchmarks: bool = False,
    github_token: str | None = None,
    context: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Compute and render CI reliability KPIs for one repository window.

    In the interactive shell the report is painted straight to the console so
    every figure the user sees is the computed one; the returned
    ``response_text`` then only summarizes. Other surfaces get the markdown.
    When ``include_benchmarks`` is true the same call also paints one comparison
    table against apache/airflow and fastapi/fastapi (snapshots first).
    """
    window = min(max(int(days or _DEFAULT_WINDOW_DAYS), _MIN_WINDOW_DAYS), _MAX_WINDOW_DAYS)
    compare = _flag(include_benchmarks)
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
    token = resolve_github_token(github_token)
    if not token:
        message = (
            f"A GitHub token is required to read the Actions history of {repo_owner}/{repo_name}. "
            "Run `opensre integrations setup github` and try again."
        )
        return tool_unavailable(_SOURCE, message, response_text=message)
    now = datetime.now(UTC)
    console = _console(context)
    snapshot = read_fresh_snapshot(
        snapshot_root(), repo_owner, repo_name, window_days=window, now=now
    )
    if snapshot is not None:
        return _from_snapshot(
            snapshot,
            repo_owner,
            repo_name,
            window,
            console,
            include_benchmarks=compare,
            token=token,
        )
    if console is not None:
        # Two-column lead matches the shell's reply gutter so the tool's lines
        # hang with the agent's notes instead of breaking the transcript edge.
        console.print(
            f"  [dim]Reading GitHub Actions history for {escape(f'{repo_owner}/{repo_name}')}, "
            f"last {window} days…[/dim]"
        )
    started = time.monotonic()
    progress = None
    if console is not None:

        def progress(line: str) -> None:
            console.print(f"  [dim]{escape(line)}[/dim]")

    try:
        analysis = analyze_repository(
            repo_owner, repo_name, token=token, days=window, now=now, progress=progress
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
    report = analysis.report
    try:
        write_snapshot(
            snapshot_root(),
            repo_owner,
            repo_name,
            now,
            {
                "generated_at": now.isoformat(),
                "window_days": window,
                "headline": headline(report),
                "report": report_to_dict(report),
                **report_payload(report),
            },
        )
    except OSError:
        # The analysis is the result; a snapshot that cannot be written only
        # means the next call reads GitHub again.
        logger.warning("Could not save the CI reliability snapshot", exc_info=True)
    if console is not None:
        console.print(
            f"  [dim]Read {analysis.runs_read} runs in {time.monotonic() - started:.0f}s.[/dim]"
        )
        console.print()
    return _result(
        report,
        repo_owner,
        repo_name,
        window,
        console,
        include_benchmarks=compare,
        token=token,
    )


__all__ = ["TOOL_NAME", "analyze_github_ci_reliability"]
