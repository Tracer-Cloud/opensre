"""Headless GitHub PR sweep for scheduled Slack delivery."""

from __future__ import annotations

import logging

from core.agent_harness import AgentSession, SessionCore
from infrastructure.scheduling.scheduler.agent_runner import AgentPayload
from infrastructure.scheduling.scheduler.schedule_cancel import cancel_requested_for_payload
from infrastructure.scheduling.scheduler.types import TaskReport
from integrations.github.app_connection import github_setup_url, refreshed_github_token
from integrations.scheduled_outcomes import ScheduledOutcomes

logger = logging.getLogger(__name__)

_PR_SWEEP_PROMPT = (
    "GitHub PR sweep for engineering standup: use summarize_github_pr_status and "
    "list_github_work_items (or the tracking-github-work-status skill) to report mergeable PRs, "
    "stale/superseded PRs, and conflicted PRs. Format a short Slack-ready plain-text "
    "digest with owners to ping. If GitHub is not configured, say so clearly."
)


def run_github_pr_sweep(payload: AgentPayload) -> TaskReport:
    """Run one headless turn that produces a PR sweep digest."""
    connection_id = str(payload.get("github_connection_id") or "") or None
    if not refreshed_github_token(connection_id):
        return TaskReport(
            f"GitHub PR sweep blocked. Connect or reconnect GitHub in the OpenSRE app: {github_setup_url()}",
            work_status="blocked",
            error_kind="github_connection_required",
        )

    def prepare_session(session: SessionCore) -> None:
        session.integrations.github_connection_id = connection_id
        session.integrations.resolved_cache = None

    result = AgentSession.run_headless_turn(
        _PR_SWEEP_PROMPT,
        prepare_session=prepare_session,
        logger=logger,
        is_tty=False,
        cancel_requested=cancel_requested_for_payload(payload),
    )
    report = result.primary_response_text
    if not result.answered or not report:
        raise RuntimeError(
            "GitHub PR sweep failed: the reasoning client did not produce a response."
        )
    return ScheduledOutcomes().report(result, agent_mode=False)


__all__ = ["run_github_pr_sweep"]
