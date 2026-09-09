"""Headless Sentry morning digest via the summarizing-sentry-issues skill."""

from __future__ import annotations

import logging
from typing import Any

from core.agent_harness import AgentSession, TurnResult
from core.agent_harness.spi.integrations import (
    merge_resolved_integrations,
    resolve_and_cache_integrations,
)
from infrastructure.harness_providers import configured_integration_services
from infrastructure.scheduling.scheduler.agent_runner import AgentPayload
from integrations.sentry.project_scope import (
    apply_sentry_project_scope,
    payload_project_slug,
)

logger = logging.getLogger(__name__)

_MORNING_DIGEST_BASE_PROMPT = (
    "Sentry morning digest: summarize unresolved Sentry issues from the last 24 hours "
    "and include uptime status from the watch transition log. "
    "Follow the summarizing-sentry-issues skill workflow."
)


def build_morning_digest_prompt(payload: AgentPayload) -> str:
    """Build the fixed headless prompt for scheduled/on-demand morning digests."""
    prompt = _MORNING_DIGEST_BASE_PROMPT
    project = payload_project_slug(payload)
    if project:
        prompt = f"{prompt} Project scope is fixed to {project!r} for this run."
    return prompt


def _apply_digest_project_scope(session: Any, payload: AgentPayload) -> None:
    """Pin ``project_slug`` on the session Sentry integration before tool calls."""
    project = payload_project_slug(payload)
    if not project:
        return
    resolved = resolve_and_cache_integrations(session)
    scoped = apply_sentry_project_scope(resolved, project)
    session.resolved_integrations_cache = merge_resolved_integrations(
        session.resolved_integrations_cache,
        {"sentry": scoped.get("sentry", {})},
    )


def _require_sentry_configured() -> None:
    if "sentry" not in configured_integration_services():
        raise RuntimeError(
            "Sentry is not configured. Run `opensre integrations setup` and verify "
            "with `opensre integrations verify sentry` before scheduling a digest."
        )


def _dispatch_headless_turn(message: str, payload: AgentPayload) -> TurnResult:
    _require_sentry_configured()
    return AgentSession.run_headless_turn(
        message,
        prepare_session=lambda session: _apply_digest_project_scope(session, payload),
        logger=logger,
        is_tty=False,
    )


def run_sentry_morning_digest(payload: AgentPayload) -> str:
    """Run one headless summarizing-sentry-issues turn and return the assistant report."""
    message = build_morning_digest_prompt(payload)
    result = _dispatch_headless_turn(message, payload)
    report = result.primary_response_text
    if not result.answered:
        if report:
            raise RuntimeError(f"Sentry morning digest failed: {report}")
        raise RuntimeError(
            "Sentry morning digest failed: the reasoning client did not produce a response."
        )
    if not report:
        raise RuntimeError(
            "Sentry morning digest failed: the reasoning client did not produce a response."
        )
    return report


__all__ = ["build_morning_digest_prompt", "run_sentry_morning_digest"]
