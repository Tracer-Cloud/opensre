"""JSON-ready figures shared by interactive and scheduled CI reports."""

from __future__ import annotations

from typing import Any

from integrations.github.tools.ci_analytics.models import CiAnalyticsReport, FailureKind


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
        "blocked_working_hours": round(report.blocked_working_minutes / 60, 1),
        "working_hours": report.working_hours_label,
        "developers_affected": report.developers_affected,
        "developers": [
            {
                "login": w.login,
                "pull_requests": w.pull_requests,
                "working_minutes": round(w.working_minutes, 1),
                "working_minutes_per_week": round(w.working_minutes_per_week, 1),
                "working_hours_per_week": round(w.working_minutes_per_week / 60, 1),
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
                "red_hours": round(s.red_hours, 2),
            }
            for s in report.workflows
        ],
        "coverage_notices": list(report.coverage_notices),
    }
