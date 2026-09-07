"""Emit API for analytics events."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from infrastructure.analytics.event_properties import (
    _bucket_duration_ms,
    _bucket_percentage,
    _integration_lifecycle_properties,
    _onboard_completed_properties,
)
from infrastructure.analytics.events import Event
from infrastructure.analytics.provider import Properties, get_analytics
from infrastructure.observability.errors.sentry import capture_exception

EVAL_AND_TERMINAL_KPI_QUERIES: Final[dict[str, str]] = {
    "terminal_action_execution_success_rate": """
SELECT
  round(
    100.0 * sum(toFloat64OrNull(properties.executed_success_count)) /
    nullIf(sum(toFloat64OrNull(properties.executed_count)), 0),
    2
  ) AS terminal_action_execution_success_rate
FROM events
WHERE event = 'terminal_actions_executed'
""".strip(),
    "terminal_fallback_rate": """
SELECT
  round(
    100.0 * countIf(
      event = 'terminal_turn_summarized'
      AND (properties.fallback_to_llm = true OR properties.fallback_to_llm = 'true')
    ) /
    nullIf(countIf(event = 'terminal_turn_summarized'), 0),
    2
  ) AS terminal_fallback_rate
FROM events
WHERE event = 'terminal_turn_summarized'
""".strip(),
}

EVAL_AND_TERMINAL_EVENT_CONTRACT: Final[dict[Event, frozenset[str]]] = {
    Event.TERMINAL_ACTIONS_PLANNED: frozenset({"planned_count", "has_unhandled_clause"}),
    Event.TERMINAL_ACTIONS_EXECUTED: frozenset(
        {"planned_count", "executed_count", "executed_success_count", "success_rate_bucket"}
    ),
    Event.TERMINAL_TURN_SUMMARIZED: frozenset(
        {
            "planned_count",
            "executed_count",
            "executed_success_count",
            "fallback_to_llm",
            "session_turn_index",
            "session_fallback_count",
            "session_action_success_bucket",
            "session_fallback_rate_bucket",
        }
    ),
}


def _capture(event: Event, properties: Properties | None = None) -> None:
    try:
        get_analytics().capture(event, properties)
    except Exception as exc:
        capture_exception(exc)


def capture_cli_invoked(properties: Properties | None = None) -> None:
    # Whole-process default for local CLI; gateway binds surface per turn instead.
    try:
        from infrastructure.analytics.usage_context import UsageSurface, ensure_process_session_id

        analytics = get_analytics()
        analytics.set_persistent_property("surface", UsageSurface.CLI)
        ensure_process_session_id()
        analytics.capture(Event.CLI_INVOKED, properties)
    except Exception as exc:
        capture_exception(exc)


def capture_gateway_turn_started(*, surface: str) -> None:
    """Mark the start of one Slack/Telegram gateway agent turn."""
    _capture(Event.GATEWAY_TURN_STARTED, {"surface": surface})


def capture_gateway_turn_completed(
    *,
    surface: str,
    duration_ms: float,
    answered: bool,
    final_intent: str | None = None,
) -> None:
    """Mark successful completion of one gateway agent turn."""
    props: Properties = {
        "surface": surface,
        "duration_ms": round(duration_ms),
        "duration_bucket": _bucket_duration_ms(duration_ms),
        "answered": answered,
    }
    if final_intent:
        props["final_intent"] = final_intent
    _capture(Event.GATEWAY_TURN_COMPLETED, props)


def capture_gateway_turn_failed(
    *,
    surface: str | None,
    duration_ms: float,
    error_type: str,
) -> None:
    """Mark a failed gateway agent turn (exception during dispatch).

    ``surface`` may be omitted when transport context was unbound so failures
    still land in PostHog for regression detection.
    """
    props: Properties = {
        "duration_ms": round(duration_ms),
        "duration_bucket": _bucket_duration_ms(duration_ms),
        "error_type": error_type,
        "surface_missing": not bool(surface),
    }
    if surface:
        props["surface"] = surface
    _capture(Event.GATEWAY_TURN_FAILED, props)


def capture_repl_execution_policy_decision(properties: Properties | None = None) -> None:
    _capture(Event.REPL_EXECUTION_POLICY_DECISION, properties)


def capture_onboard_started() -> None:
    _capture(Event.ONBOARD_STARTED)


def capture_onboard_completed(config: Mapping[str, object]) -> None:
    _capture(Event.ONBOARD_COMPLETED, _onboard_completed_properties(config))


def capture_onboard_failed() -> None:
    _capture(Event.ONBOARD_FAILED)


def capture_integration_setup_started(service: str) -> None:
    _capture(Event.INTEGRATION_SETUP_STARTED, _integration_lifecycle_properties(service))


def capture_integration_setup_completed(service: str) -> None:
    _capture(Event.INTEGRATION_SETUP_COMPLETED, _integration_lifecycle_properties(service))


def capture_integrations_listed() -> None:
    _capture(Event.INTEGRATIONS_LISTED)


def capture_integration_removed(service: str) -> None:
    _capture(Event.INTEGRATION_REMOVED, _integration_lifecycle_properties(service))


def capture_integration_verified(service: str) -> None:
    _capture(Event.INTEGRATION_VERIFIED, _integration_lifecycle_properties(service))


def capture_loop_suggestion_prompted() -> None:
    """Exposure event: the suggested-loops startup picker was rendered."""
    _capture(Event.LOOP_SUGGESTION_PROMPTED)


def capture_loop_suggestion_selected(*, option: str) -> None:
    """User picked one of the suggested loop options (ci_cd / task_management / daily_brief)."""
    _capture(Event.LOOP_SUGGESTION_SELECTED, {"option": option})


def capture_loop_suggestion_skipped() -> None:
    """User dismissed the suggested-loops picker (Escape) without choosing."""
    _capture(Event.LOOP_SUGGESTION_SKIPPED)


def capture_onboarding_demo_prompted() -> None:
    """Exposure event: the onboarding demo picker was rendered."""
    _capture(Event.ONBOARDING_DEMO_PROMPTED)


def capture_onboarding_demo_selected(*, option: str, custom: bool) -> None:
    """User picked a demo; ``custom`` marks a typed answer instead of a listed option."""
    _capture(Event.ONBOARDING_DEMO_SELECTED, {"option": option, "custom": custom})


def capture_onboarding_demo_skipped() -> None:
    """User dismissed the onboarding demo picker without choosing."""
    _capture(Event.ONBOARDING_DEMO_SKIPPED)


def capture_terminal_actions_planned(*, planned_count: int, has_unhandled_clause: bool) -> None:
    _capture(
        Event.TERMINAL_ACTIONS_PLANNED,
        {
            "planned_count": planned_count,
            "has_unhandled_clause": has_unhandled_clause,
        },
    )


def capture_terminal_actions_executed(
    *,
    planned_count: int,
    executed_count: int,
    executed_success_count: int,
) -> None:
    success_percent = 100.0 * executed_success_count / executed_count if executed_count > 0 else 0.0
    _capture(
        Event.TERMINAL_ACTIONS_EXECUTED,
        {
            "planned_count": planned_count,
            "executed_count": executed_count,
            "executed_success_count": executed_success_count,
            "success_rate_bucket": _bucket_percentage(success_percent),
        },
    )


def capture_react_turn_completed(
    *,
    phase: str,
    llm_iterations_used: int,
    llm_iteration_cap: int,
    hit_iteration_cap: bool,
    stop_reason: str,
    tool_calls_executed: int,
    duration_ms: int,
    cli_session_id: str,
    cli_turn_kind: str,
    llm_provider: str,
    llm_model: str,
    prompt_turn_id: str | None = None,
) -> None:
    properties: Properties = {
        "phase": phase,
        "llm_iterations_used": llm_iterations_used,
        "llm_iteration_cap": llm_iteration_cap,
        "hit_iteration_cap": hit_iteration_cap,
        "stop_reason": stop_reason,
        "tool_calls_executed": tool_calls_executed,
        "duration_ms": duration_ms,
        "cli_session_id": cli_session_id,
        "cli_turn_kind": cli_turn_kind,
        "llm_provider": llm_provider,
        "llm_model": llm_model,
    }
    if prompt_turn_id:
        properties["prompt_turn_id"] = prompt_turn_id
    _capture(Event.REACT_TURN_COMPLETED, properties)


def capture_terminal_turn_summarized(
    *,
    planned_count: int,
    executed_count: int,
    executed_success_count: int,
    fallback_to_llm: bool,
    session_turn_index: int,
    session_fallback_count: int,
    session_action_success_percent: float,
    session_fallback_rate_percent: float,
) -> None:
    _capture(
        Event.TERMINAL_TURN_SUMMARIZED,
        {
            "planned_count": planned_count,
            "executed_count": executed_count,
            "executed_success_count": executed_success_count,
            "fallback_to_llm": fallback_to_llm,
            "session_turn_index": session_turn_index,
            "session_fallback_count": session_fallback_count,
            "session_action_success_bucket": _bucket_percentage(session_action_success_percent),
            "session_fallback_rate_bucket": _bucket_percentage(session_fallback_rate_percent),
        },
    )


def capture_update_started(*, check_only: bool) -> None:
    _capture(Event.UPDATE_STARTED, {"check_only": check_only})


def capture_update_completed(*, check_only: bool, updated: bool) -> None:
    _capture(Event.UPDATE_COMPLETED, {"check_only": check_only, "updated": updated})


def capture_update_failed(*, check_only: bool, reason: str) -> None:
    _capture(Event.UPDATE_FAILED, {"check_only": check_only, "reason": reason})


def capture_agent_secret_detected(
    *,
    rule_names: tuple[str, ...],
    count: int,
    blocked: bool,
) -> None:
    _capture(
        Event.AGENT_SECRET_DETECTED,
        {"rule_names": ",".join(rule_names), "count": count, "blocked": blocked},
    )
