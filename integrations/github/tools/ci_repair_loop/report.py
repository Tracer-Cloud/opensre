"""One evidence-backed summary for the foreground and scheduler history."""

from __future__ import annotations

import time
from pathlib import Path

from integrations.github.tools.ci_repair_loop.models import RepairRun, RepairStatus


def render_report(run: RepairRun, directory: Path) -> str:
    """Render the durable result without inventing evidence for unfinished stages."""
    elapsed = max(0, int((run.finished_at or time.time()) - run.started_at))
    pr = f"[#{run.pr_number}]({run.pr_url})" if run.pr_url else "Not created."
    commit = (
        f"[{run.fixed_sha[:12]}]({run.repository_url}/commit/{run.fixed_sha})"
        if run.fixed_sha
        else "No verified repair commit."
    )
    failure = f"[Failing run]({run.failed_run_url})" if run.failed_run_url else "Not observed."
    passing = f"[Passing run]({run.passed_run_url})" if run.passed_run_url else "Not verified."
    next_step = (
        "Run the demo again or schedule repair for another PR."
        if run.status is RepairStatus.SUCCEEDED
        else "Inspect the retained PR and diagnostics before starting another repair."
    )
    diagnostics = f"[Worker log]({directory / 'worker.log'})"
    if run.attempts:
        diagnostics += f" [Latest attempt]({directory / f'attempt-{run.attempts}.json'})"
    schedule = (
        "Stopped; history retained." if run.terminal else "Active; original deadline retained."
    )
    return "\n".join(
        [
            f"- **Outcome:** {run.status.value}. {run.reason}",
            f"- **Elapsed:** {elapsed // 60}m {elapsed % 60}s; ten-minute maximum.",
            f"- **Repository:** [{run.owner}/{run.repo}]({run.repository_url})",
            f"- **Pull request:** {pr}",
            f"- **Repair attempts:** {run.attempts}; outcomes: {', '.join(run.attempt_errors) or 'none yet'}.",
            f"- **CI evidence:** {failure} {passing}",
            f"- **Repair commit:** {commit}",
            f"- **Scheduling:** {schedule} `/loops show {run.id}`",
            f"- **Artifacts:** {run.cleanup} [Report]({directory / 'result.md'}) {diagnostics}",
            f"- **Next steps:** {next_step}",
        ]
    )
