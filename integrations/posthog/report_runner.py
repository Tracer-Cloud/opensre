"""Headless PostHog per-metric report via the summarizing-posthog-analytics skill.

Mirrors :mod:`integrations.sentry.morning_digest_runner`: run one headless
agent turn driven by the ``summarizing-posthog-analytics`` skill and return the assistant
report text. Used by the ``opensre posthog report`` command and the scheduled
delivery path (issue #3824).
"""

from __future__ import annotations

import logging

from core.agent_harness import AgentSession, TurnResult
from infrastructure.scheduling.scheduler.agent_runner import AgentPayload
from integrations.posthog.report_prerequisites import (
    DEFAULT_POSTHOG_PERIOD,
    posthog_not_configured_hint,
    posthog_report_available,
)

logger = logging.getLogger(__name__)

_REPORT_BASE_PROMPT = (
    "PostHog analytics report: produce a per-metric product-analytics pulse "
    "for the team — what moved and why it matters. "
    "Follow the summarizing-posthog-analytics skill workflow."
)


def _payload_stats_period(payload: AgentPayload) -> str:
    period = str(payload.get("stats_period") or "").strip()
    return period or DEFAULT_POSTHOG_PERIOD


def _payload_metrics(payload: AgentPayload) -> str:
    return str(payload.get("metrics") or "").strip()


def build_report_prompt(payload: AgentPayload) -> str:
    """Build the fixed headless prompt for scheduled/on-demand metric reports."""
    prompt = f"{_REPORT_BASE_PROMPT} Use a {_payload_stats_period(payload)} window."
    metrics = _payload_metrics(payload)
    if metrics:
        prompt = f"{prompt} Focus on these metrics: {metrics}."
    return prompt


def _require_posthog_configured() -> None:
    if posthog_report_available():
        return
    raise RuntimeError(posthog_not_configured_hint())


def _dispatch_headless_turn(message: str) -> TurnResult:
    _require_posthog_configured()
    return AgentSession.run_headless_turn(
        message,
        logger=logger,
        is_tty=False,
    )


def run_posthog_report(payload: AgentPayload) -> str:
    """Run one headless summarizing-posthog-analytics turn and return the assistant report."""
    message = build_report_prompt(payload)
    result = _dispatch_headless_turn(message)
    report = result.primary_response_text
    if not result.answered:
        # Billing/auth failures often leave a useful message on the action path
        # without an ``llm_run`` (so ``answered`` is False). Surface that text
        # instead of a opaque "no response" error.
        if report:
            raise RuntimeError(f"PostHog report failed: {report}")
        raise RuntimeError(
            "PostHog report failed: the reasoning client did not produce a response."
        )
    if not report:
        raise RuntimeError(
            "PostHog report failed: the reasoning client did not produce a response."
        )
    return report


__all__ = ["build_report_prompt", "run_posthog_report"]
