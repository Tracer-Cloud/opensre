"""The registry of scheduled headless runners, keyed by the payload's ``source``."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from infrastructure.scheduling.scheduler.agent_runner import AgentPayload
from infrastructure.scheduling.scheduler.sources import (
    CLI_POSTHOG_METRIC_REPORT,
    CLI_SENTRY_MORNING_DIGEST,
    SCHEDULED_GITHUB_PR_SWEEP,
    SCHEDULED_MANUAL_LOOP,
    SCHEDULED_POSTHOG_METRIC_REPORT,
    SCHEDULED_RECURRING_SKILL,
    SCHEDULED_SENTRY_MORNING_DIGEST,
    SCHEDULED_SENTRY_UPTIME_WATCH,
)
from infrastructure.scheduling.scheduler.types import TaskReport
from integrations.github.pr_sweep_runner import run_github_pr_sweep
from integrations.manual_loop_runner import report_builder, run_manual_prompt_loop
from integrations.posthog.report_runner import run_posthog_report
from integrations.scheduled_skill_runner import run_scheduled_recurring_skill
from integrations.sentry.morning_digest_runner import run_sentry_morning_digest
from integrations.sentry.uptime import run_uptime_watch_tick


def _every_run(_payload: AgentPayload) -> bool:
    return True


def _unless_report_builder(payload: AgentPayload) -> bool:
    """A manual loop with a deterministic report builder calls no model."""
    return report_builder(payload) is None


def _run_uptime_watch(payload: AgentPayload) -> TaskReport:
    return TaskReport(
        run_uptime_watch_tick(
            task_id=str(payload.get("task_id") or "cli"),
            project_slug=str(payload.get("project_slug") or "").strip(),
        )
    )


@dataclass(frozen=True)
class ScheduledRunner:
    """One headless runner and whether a run of it costs a model turn."""

    run: Callable[[AgentPayload], TaskReport]
    runs_model_turn: Callable[[AgentPayload], bool] = _every_run


RUNNERS: Mapping[str, ScheduledRunner] = {
    SCHEDULED_SENTRY_MORNING_DIGEST: ScheduledRunner(run_sentry_morning_digest),
    CLI_SENTRY_MORNING_DIGEST: ScheduledRunner(run_sentry_morning_digest),
    SCHEDULED_SENTRY_UPTIME_WATCH: ScheduledRunner(_run_uptime_watch),
    SCHEDULED_GITHUB_PR_SWEEP: ScheduledRunner(run_github_pr_sweep),
    SCHEDULED_POSTHOG_METRIC_REPORT: ScheduledRunner(run_posthog_report),
    CLI_POSTHOG_METRIC_REPORT: ScheduledRunner(run_posthog_report),
    SCHEDULED_MANUAL_LOOP: ScheduledRunner(run_manual_prompt_loop, _unless_report_builder),
    SCHEDULED_RECURRING_SKILL: ScheduledRunner(run_scheduled_recurring_skill),
}


def _registered(payload: AgentPayload) -> ScheduledRunner:
    source = str(payload.get("source") or "")
    runner = RUNNERS.get(source)
    if runner is None:
        raise RuntimeError(f"No scheduled runner is registered for source {source!r}.")
    return runner


def runs_model_turn(payload: AgentPayload) -> bool:
    """Whether running ``payload`` costs a model turn, as its registered runner declares."""
    runner = _registered(payload)
    return runner.runs_model_turn(payload)


def run_scheduled_agent_digest(payload: AgentPayload) -> TaskReport:
    """Run ``payload`` with the runner registered for its ``source``."""
    runner = _registered(payload)
    return runner.run(payload)


__all__ = ["RUNNERS", "ScheduledRunner", "run_scheduled_agent_digest", "runs_model_turn"]
