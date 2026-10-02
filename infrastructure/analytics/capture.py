"""Emit API for analytics events."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final, cast
from uuid import uuid4

from infrastructure.analytics.event_properties import (
    _bounded_redacted_text,
    _bucket_duration_ms,
    _bucket_percentage,
    _integration_lifecycle_properties,
    _onboard_completed_properties,
)
from infrastructure.analytics.events import Event, cli_command_event_name
from infrastructure.analytics.provider import (
    JsonValue,
    Properties,
    analytics_opted_out,
    get_analytics,
)
from infrastructure.observability.errors.sentry import capture_exception

_ASK_USER_LABEL_MAX_CHARS: Final[int] = 80
_ASK_USER_TITLE_MAX_CHARS: Final[int] = 500
_ASK_USER_OPTION_MAX_CHARS: Final[int] = 300
_TOOL_ERROR_MESSAGE_MAX_CHARS: Final[int] = 500

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


def capture_cli_invoked(
    properties: Properties | None = None, command_parts: Sequence[str] = ()
) -> None:
    # Whole-process default for local CLI; gateway binds surface per turn instead.
    try:
        from infrastructure.analytics.usage_context import UsageSurface, ensure_process_session_id

        analytics = get_analytics()
        analytics.set_persistent_property("surface", UsageSurface.CLI)
        ensure_process_session_id()
        analytics.capture(cli_command_event_name(command_parts), properties)
    except Exception as exc:
        capture_exception(exc)


def capture_account_authenticated() -> None:
    """Link this installation ID to server-resolved account identity."""
    try:
        analytics = get_analytics()
        analytics.refresh_destination()
        analytics.capture(Event.ACCOUNT_AUTHENTICATED)
    except Exception as exc:
        capture_exception(exc)


def capture_sign_in_prompted() -> None:
    """Exposure event: the mandatory sign-in screen was rendered to a signed-out user."""
    _capture(Event.SIGN_IN_PROMPTED, {"entrypoint": "sign_in_gate"})


def capture_sign_in_selected(*, choice_label: str) -> None:
    """User picked sign-in on the gate; ``account_authenticated`` reports the outcome."""
    _capture(
        Event.SIGN_IN_SELECTED,
        {"choice_label": choice_label, "method": "menu", "entrypoint": "sign_in_gate"},
    )


def capture_stay_signed_out_selected(*, choice_label: str, method: str) -> None:
    """User left the gate signed out: ``menu`` picked the exit option, ``dismissed`` closed the menu."""
    _capture(
        Event.STAY_SIGNED_OUT_SELECTED,
        {"choice_label": choice_label, "method": method, "entrypoint": "sign_in_gate"},
    )


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
    still land in product analytics for regression detection.
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
    properties: Properties = {
        "planned_count": planned_count,
        "executed_count": executed_count,
        "executed_success_count": executed_success_count,
    }
    if executed_count > 0:
        properties["success_rate_bucket"] = _bucket_percentage(
            100.0 * executed_success_count / executed_count
        )
    _capture(
        Event.TERMINAL_ACTIONS_EXECUTED,
        properties,
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
    session_action_success_percent: float | None,
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
            **(
                {
                    "session_action_success_bucket": _bucket_percentage(
                        session_action_success_percent
                    )
                }
                if session_action_success_percent is not None
                else {}
            ),
            "session_fallback_rate_bucket": _bucket_percentage(session_fallback_rate_percent),
        },
    )


def capture_agent_tool_call_completed(
    *,
    tool_call_id: str,
    tool_name: str,
    source: str,
    role: str,
    outcome: str,
    executed: bool,
    is_error: bool,
    terminate: bool,
    duration_ms: int,
    work_status: str = "",
    error_message: str = "",
) -> None:
    """Record the privacy-safe outcome of one model-requested tool call.

    A failed call also carries ``error_message``: the tool's own description of
    what went wrong, redacted and length-capped. Arguments and result evidence
    stay off the event.
    """
    properties: Properties = {
        "tool_call_id": tool_call_id,
        "tool_name": tool_name,
        "source": source,
        "role": role,
        "outcome": outcome,
        "executed": executed,
        "is_error": is_error,
        "terminate": terminate,
        "duration_ms": duration_ms,
        "duration_bucket": _bucket_duration_ms(duration_ms),
        "work_status": work_status,
    }
    recorded_error = _bounded_redacted_text(error_message, max_chars=_TOOL_ERROR_MESSAGE_MAX_CHARS)
    if is_error and recorded_error:
        properties["error_message"] = recorded_error
    _capture(Event.AGENT_TOOL_CALL_COMPLETED, properties)


def _ask_user_questions(
    questions: Sequence[Mapping[str, object]],
) -> list[dict[str, JsonValue]]:
    sanitized: list[dict[str, JsonValue]] = []
    for question in questions:
        raw_options = question.get("options")
        options = (
            raw_options
            if isinstance(raw_options, Sequence) and not isinstance(raw_options, str)
            else ()
        )
        sanitized.append(
            {
                "label": _bounded_redacted_text(
                    question.get("label", ""), max_chars=_ASK_USER_LABEL_MAX_CHARS
                ),
                "title": _bounded_redacted_text(
                    question.get("title", ""), max_chars=_ASK_USER_TITLE_MAX_CHARS
                ),
                "options": [
                    _bounded_redacted_text(option, max_chars=_ASK_USER_OPTION_MAX_CHARS)
                    for option in options
                ],
                "multi_select": bool(question.get("multi_select", False)),
            }
        )
    return sanitized


def _with_optional_skill(properties: Properties, skill_name: str | None) -> Properties:
    if skill_name:
        properties["skill_name"] = skill_name
    return properties


def capture_ask_user_prompt_rendered(
    *,
    interaction_id: str,
    questions: Sequence[Mapping[str, object]],
    render_mode: str,
    allow_custom: bool,
    has_command_options: bool,
    skill_name: str | None,
) -> None:
    """Record a structured Ask User prompt when it becomes visible."""
    sanitized = _ask_user_questions(questions)
    _capture(
        Event.ASK_USER_PROMPT_RENDERED,
        _with_optional_skill(
            {
                "interaction_id": interaction_id,
                "prompt_kind": "batch" if len(sanitized) > 1 else "single",
                "question_count": len(sanitized),
                "questions": cast(list[JsonValue], sanitized),
                "render_mode": render_mode,
                "allow_custom": allow_custom,
                "has_command_options": has_command_options,
            },
            skill_name,
        ),
    )


def capture_ask_user_prompt_answered(
    *,
    interaction_id: str,
    selected_option_indices: Sequence[Sequence[int]],
    custom_answers: Sequence[str | None],
    disposition: str,
    skill_name: str | None,
) -> None:
    """Record listed/custom options selected from a rendered Ask User prompt."""
    answer_details: list[JsonValue] = []
    for index, (indices, custom_answer) in enumerate(
        zip(selected_option_indices, custom_answers, strict=True)
    ):
        detail: dict[str, JsonValue] = {
            "question_index": index,
            "selected_option_indices": list(indices),
            "custom": custom_answer is not None,
        }
        if custom_answer is not None:
            detail["answer"] = _bounded_redacted_text(
                custom_answer, max_chars=_ASK_USER_TITLE_MAX_CHARS
            )
        answer_details.append(detail)
    _capture(
        Event.ASK_USER_PROMPT_ANSWERED,
        _with_optional_skill(
            {
                "interaction_id": interaction_id,
                "question_count": len(answer_details),
                "answers": answer_details,
                "disposition": disposition,
            },
            skill_name,
        ),
    )


def capture_ask_user_prompt_dismissed(
    *, interaction_id: str, reason: str, skill_name: str | None
) -> None:
    """Record a rendered Ask User prompt closed without an answer."""
    _capture(
        Event.ASK_USER_PROMPT_DISMISSED,
        _with_optional_skill(
            {"interaction_id": interaction_id, "reason": reason},
            skill_name,
        ),
    )


def capture_interactive_shell_rendered(*, entrypoint: str) -> None:
    """Record first interactive-shell chrome, including the sign-in screen.

    The REPL entrypoint suppresses this for ``--resume`` and for an auto-launch
    after ``opensre onboard``. CLI subcommands never call it.
    """
    _capture(Event.INTERACTIVE_SHELL_RENDERED, {"entrypoint": entrypoint})


def begin_cli_auth_attempt() -> str | None:
    """Record a non-secret browser handoff even when the URL will be opened manually."""
    if analytics_opted_out():
        return None
    attempt_id = str(uuid4())
    _capture(Event.CLI_AUTH_STARTED, {"cli_auth_attempt_id": attempt_id})
    return attempt_id


def capture_browser_open_requested(
    *, target: str, opened: bool, cli_auth_attempt_id: str | None = None
) -> None:
    """Record an application-requested browser open without retaining its URL."""
    properties: Properties = {"target": target, "opened": opened}
    if cli_auth_attempt_id:
        properties["cli_auth_attempt_id"] = cli_auth_attempt_id
    _capture(Event.BROWSER_OPEN_REQUESTED, properties)


def capture_skill_executed(
    *,
    skill_name: str,
    entrypoint: str,
    skills_release: str | None = None,
    skills_source: str | None = None,
    skill_version: str | None = None,
    skill_digest: str | None = None,
) -> None:
    """Record one successful entry into an OpenSRE skill workflow and which content ran.

    ``skills_release`` names the active catalog (``remote:<seq>``, ``bundled:<version>``
    or ``override:<digest>``) so outcomes can be compared per published release.
    """
    properties: Properties = {"skill_name": skill_name, "entrypoint": entrypoint}
    provenance = {
        "skills_release": skills_release,
        "skills_source": skills_source,
        "skill_version": skill_version,
        "skill_digest": skill_digest,
    }
    properties.update({key: value for key, value in provenance.items() if value})
    _capture(Event.SKILL_EXECUTED, properties)


def capture_skills_release_activated(
    *, skills_release: str, skills_source: str, previous_release: str | None, skill_count: int
) -> None:
    """Record that this process switched to a different skills catalog."""
    properties: Properties = {
        "skills_release": skills_release,
        "skills_source": skills_source,
        "skill_count": skill_count,
    }
    if previous_release:
        properties["previous_release"] = previous_release
    _capture(Event.SKILLS_RELEASE_ACTIVATED, properties)


def capture_skill_value_delivered(
    *, skill_name: str, insight: str, prompt_turn_id: str | None = None
) -> None:
    """Record the business insight that successfully reached a skill's output sink."""
    properties: Properties = {
        "skill_name": skill_name,
        "insight": _bounded_redacted_text(insight, max_chars=4000),
    }
    if prompt_turn_id:
        properties["prompt_turn_id"] = prompt_turn_id
    _capture(Event.SKILL_VALUE_DELIVERED, properties)


def capture_opensre_commit_created(
    *, workflow: str, commit_kind: str, changed_file_count: int
) -> None:
    """Record a git commit successfully created by an OpenSRE workflow."""
    _capture(
        Event.OPENSRE_COMMIT_CREATED,
        {
            "workflow": workflow,
            "commit_kind": commit_kind,
            "changed_file_count": changed_file_count,
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
        Event.AGENT_EXPOSURE_DETECTED,
        {"rule_names": ",".join(rule_names), "count": count, "blocked": blocked},
    )


def capture_hosted_gateway_task_submitted(prompt_id: str) -> None:
    """A new prompt was accepted by the managed gateway; polls do not emit this."""
    _capture(Event.HOSTED_GATEWAY_TASK_SUBMITTED, {"prompt_id": prompt_id})


def capture_hosted_gateway_started(
    *, gateway_id: str, actual_state: str, already_running: bool
) -> None:
    """The app accepted a start of the organization's hosted gateway; it may still be coming up."""
    _capture(
        Event.HOSTED_GATEWAY_STARTED,
        {
            "gateway_id": gateway_id,
            "actual_state": actual_state,
            "already_running": already_running,
        },
    )


def capture_hosted_gateway_healthy(*, gateway_id: str, tool_name: str) -> None:
    """A health read found the organization's hosted gateway running with nothing pending."""
    _capture(Event.HOSTED_GATEWAY_HEALTHY, {"gateway_id": gateway_id, "tool_name": tool_name})


def _ci_repair_properties(
    repair_run_id: str, repository: str, pr_number: int, demo: bool
) -> Properties:
    # ``repair_run_id`` joins the repair's events across the shell, gateway, and worker.
    properties: Properties = {
        "repair_run_id": repair_run_id,
        "repository": repository,
        "demo": demo,
    }
    if pr_number:
        properties["pr_number"] = pr_number
    return properties


def capture_remote_ci_monitoring_started(
    *, repair_run_id: str, repository: str, pr_number: int, demo: bool
) -> None:
    """A gateway's own scheduler registered a CI repair loop, so it runs without the shell."""
    _capture(
        Event.REMOTE_CI_MONITORING_STARTED,
        _ci_repair_properties(repair_run_id, repository, pr_number, demo),
    )


def capture_test_ci_failure_triggered(
    *, repair_run_id: str, repository: str, pr_number: int, demo: bool, remote: bool
) -> None:
    """The repair demo opened its pull request with a failing test; ``remote`` names the host."""
    properties = _ci_repair_properties(repair_run_id, repository, pr_number, demo)
    properties["remote"] = remote
    _capture(Event.TEST_CI_FAILURE_TRIGGERED, properties)


def capture_remote_ci_failure_detected(
    *, repair_run_id: str, repository: str, pr_number: int, demo: bool
) -> None:
    """A remote repair loop saw its pull request fail CI and started its first repair attempt."""
    _capture(
        Event.REMOTE_CI_FAILURE_DETECTED,
        _ci_repair_properties(repair_run_id, repository, pr_number, demo),
    )


def capture_remote_ci_repair_succeeded(
    *,
    repair_run_id: str,
    repository: str,
    pr_number: int,
    demo: bool,
    attempts: int,
    duration_ms: float,
) -> None:
    """A remote repair loop's own commit passed CI; ``duration_ms`` counts from scheduling."""
    properties = _ci_repair_properties(repair_run_id, repository, pr_number, demo)
    properties["attempts"] = attempts
    properties["duration_ms"] = round(duration_ms)
    _capture(Event.REMOTE_CI_REPAIR_SUCCEEDED, properties)
