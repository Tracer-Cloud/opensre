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
from integrations.github.tools.ci_analytics.benchmarks import MEASURED_ON
from integrations.github.tools.ci_analytics.loop import LOOP_WINDOW_DAYS
from integrations.github.tools.ci_analytics.models import CiAnalyticsReport, FailureKind
from integrations.github.tools.ci_analytics.render import (
    comparison_markdown,
    format_minutes,
    headline,
    key_results_payload,
    peer_benchmarks,
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
    include_benchmarks: bool = True,
    compact: bool = False,
) -> dict[str, Any] | None:
    """Answer from a saved report, or ``None`` when it holds no usable report object."""
    saved = snapshot.get("report")
    report = report_from_dict(saved) if isinstance(saved, dict) else None
    if report is None:
        return None
    generated = str(snapshot.get("generated_at", ""))[:16].replace("T", " ")
    if console is not None:
        console.print(
            f"  [dim]Using the CI reliability snapshot of {escape(f'{owner}/{repo}')} "
            f"from {generated} UTC (same {window}-day window).[/dim]"
        )
        console.print()
    result = _result(
        report,
        owner,
        repo,
        window,
        console,
        include_benchmarks=include_benchmarks,
        compact=compact,
    )
    result["summary"] += f" Figures as of {generated} UTC, from the saved snapshot."
    result["from_snapshot"] = snapshot.get("generated_at")
    return result


def report_text_from_snapshot(
    owner: str, repo: str, *, days: int = _DEFAULT_WINDOW_DAYS, include_benchmarks: bool = True
) -> tuple[str, str]:
    """``(markdown, generated_at)`` from today's snapshot, or ``("", "")`` when none.

    Compact: the cost sentence, key results and the comparison, without the
    counts appendix. Reads saved snapshots only; never resolves a token or
    starts a live fetch.
    """
    now = datetime.now(UTC)
    # The scheduled loop saves its own window; a same-day loop report counts too.
    for window in dict.fromkeys((days, LOOP_WINDOW_DAYS)):
        snapshot = read_fresh_snapshot(snapshot_root(), owner, repo, window_days=window, now=now)
        if snapshot is not None:
            break
    else:
        return "", ""
    saved = snapshot.get("report")
    if not isinstance(saved, dict):
        return "", ""
    report = report_from_dict(saved)
    text = render_markdown(report, compact=True)
    if include_benchmarks:
        # This report sits above the schedule card, so the next step is already taken.
        compare = comparison_markdown(report, peer_benchmarks(report), next_step=False)
        text = f"{text}\n\n{compare}"
    return text.strip(), str(snapshot.get("generated_at", ""))


def _attach_benchmarks(
    result: dict[str, Any],
    report: CiAnalyticsReport,
    *,
    console: Any,
) -> dict[str, Any]:
    """Add the comparison against the benchmark figures shipped with the product."""
    peers = peer_benchmarks(report)
    result["benchmarks"] = [
        {"owner": item.owner, "repo": item.repo, "figures": dict(item.figures)} for item in peers
    ]
    result["benchmarks_measured_on"] = MEASURED_ON.isoformat()
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
    include_benchmarks: bool = True,
    compact: bool = False,
) -> dict[str, Any]:
    """The tool's return for ``report``: painted in the shell, markdown elsewhere.

    ``compact`` drops the counts appendix from both forms.
    """
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
        # The painted report is the turn's output; a reply restating its figures
        # would print them twice.
        render_report(console, report, compact=compact)
        result = {**base, "coverage_notices": list(report.coverage_notices)}
    else:
        result = {
            **base,
            **report_payload(report),
            "response_text": render_markdown(report, compact=compact),
        }
    if include_benchmarks:
        result = _attach_benchmarks(result, report, console=console)
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
        "Read-only. A saved report from today answers without another GitHub "
        "read; a first run needs a token. Every report also carries a "
        "comparison with apache/airflow and fastapi/fastapi from figures "
        "shipped with the product, so a first run compares as well as a later "
        "one. It cannot be turned off, so never offer to skip it. The report "
        "is painted on screen — do not restate its figures."
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
        "key_results": "The takeaway rows, red time first, even when the shell painted the report",
        "response_text": "The rendered report, or a one-line summary when the shell painted it",
        "benchmarks": "Airflow and FastAPI rows from figures shipped with the product",
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
            "compact": {
                "type": "boolean",
                "description": (
                    "Key results and the comparison only, without the counts appendix "
                    "(executions, failure classification, blocked time, workflows). "
                    "Use for a first-look report. Default false."
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
    compact: bool = False,
    github_token: str | None = None,
    context: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Compute and render CI reliability KPIs for one repository window.

    In the interactive shell the report is painted straight to the console so
    every figure the user sees is the computed one; the returned
    ``response_text`` then only summarizes. Other surfaces get the markdown.
    The same call also paints one comparison table against the benchmark
    figures shipped with the product, so a first run compares as well as a
    hundredth. A saved report from today is reused; a miss reads GitHub and
    writes the snapshot. The comparison is not the model's choice to make;
    ``compact`` drops the counts appendix.
    """
    window = min(max(int(days or _DEFAULT_WINDOW_DAYS), _MIN_WINDOW_DAYS), _MAX_WINDOW_DAYS)
    brief = _flag(compact)
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
    now = datetime.now(UTC)
    console = _console(context)
    snapshot = read_fresh_snapshot(
        snapshot_root(), repo_owner, repo_name, window_days=window, now=now
    )
    token = resolve_github_token(github_token)
    if snapshot is not None:
        answered = _from_snapshot(
            snapshot,
            repo_owner,
            repo_name,
            window,
            console,
            compact=brief,
        )
        if answered is not None:
            return answered
    if not token:
        message = (
            f"A GitHub token is required to read the Actions history of {repo_owner}/{repo_name}. "
            "Run `opensre integrations setup github` and try again."
        )
        return tool_unavailable(_SOURCE, message, response_text=message)
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
        # leaves the schedule card without today's report beside it.
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
        compact=brief,
    )


__all__ = ["report_text_from_snapshot", "TOOL_NAME", "analyze_github_ci_reliability"]
