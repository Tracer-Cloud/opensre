"""Multiplex scheduled headless digests onto the shared agent_runner slot."""

from __future__ import annotations

from infrastructure.scheduling.scheduler.agent_runner import AgentPayload
from infrastructure.scheduling.scheduler.types import TaskReport
from integrations.github.pr_sweep_runner import run_github_pr_sweep
from integrations.manual_loop_runner import run_manual_prompt_loop
from integrations.posthog.report_runner import run_posthog_report
from integrations.scheduled_skill_runner import run_scheduled_recurring_skill
from integrations.sentry.morning_digest_runner import run_sentry_morning_digest
from integrations.sentry.uptime import run_uptime_watch_tick


def run_scheduled_agent_digest(payload: AgentPayload) -> TaskReport:
    """Route by ``payload['source']`` to the matching scheduled headless runner."""
    source = str(payload.get("source") or "")
    if "scheduled_recurring_skill" in source:
        return run_scheduled_recurring_skill(payload)
    if "manual_loop" in source or payload.get("loop_prompt"):
        return run_manual_prompt_loop(payload)
    if "uptime_watch" in source:
        return TaskReport(
            run_uptime_watch_tick(
                task_id=str(payload.get("task_id") or "cli"),
                project_slug=str(payload.get("project_slug") or "").strip(),
            )
        )
    if "github_pr" in source:
        return run_github_pr_sweep(payload)
    if "posthog" in source:
        return run_posthog_report(payload)
    return run_sentry_morning_digest(payload)


__all__ = ["run_scheduled_agent_digest"]
