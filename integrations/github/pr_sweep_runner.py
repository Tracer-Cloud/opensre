"""Headless GitHub PR sweep for scheduled Slack delivery."""

from __future__ import annotations

import logging

from core.agent_harness import AgentSession
from infrastructure.harness_providers import configured_integration_services
from infrastructure.scheduling.scheduler.agent_runner import AgentPayload

logger = logging.getLogger(__name__)

_PR_SWEEP_PROMPT = (
    "GitHub PR sweep for engineering standup: use summarize_github_pr_status and "
    "list_github_work_items (or the tracking-github-work-status skill) to report mergeable PRs, "
    "stale/superseded PRs, and conflicted PRs. Format a short Slack-ready plain-text "
    "digest with owners to ping. If GitHub is not configured, say so clearly."
)


def _require_github_configured() -> None:
    if "github" not in configured_integration_services():
        raise RuntimeError(
            "GitHub is not configured. Run `opensre integrations setup github` and verify "
            "with `opensre integrations verify github` before scheduling a PR sweep."
        )


def run_github_pr_sweep(payload: AgentPayload) -> str:
    """Run one headless turn that produces a PR sweep digest."""
    del payload  # reserved for future repo/org scoping
    _require_github_configured()

    result = AgentSession.run_headless_turn(
        _PR_SWEEP_PROMPT,
        logger=logger,
        is_tty=False,
    )
    report = result.primary_response_text
    if not result.answered or not report:
        raise RuntimeError(
            "GitHub PR sweep failed: the reasoning client did not produce a response."
        )
    return report


__all__ = ["run_github_pr_sweep"]
