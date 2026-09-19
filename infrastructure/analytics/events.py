"""Analytics event definitions."""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum


def cli_command_event_name(command_parts: Sequence[str]) -> str:
    """Name an invocation from registered command tokens, excluding all operands."""
    tokens = [part.lower().replace("-", "_") for part in command_parts]
    if any(re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", token) is None for token in tokens):
        raise ValueError("CLI analytics requires registered command names")
    name = "_".join(("cli_command_opensre", *tokens))
    if len(name) > 128:
        raise ValueError("CLI analytics event name exceeds 128 characters")
    return name


class Event(StrEnum):
    # Lifecycle
    ACCOUNT_AUTHENTICATED = "account_authenticated"
    # Mandatory interactive-shell sign-in gate: exposure, then one explicit
    # choice per menu round. Choosing sign-in is intent only; the account link
    # is ``account_authenticated``.
    SIGN_IN_PROMPTED = "sign_in_prompted"
    SIGN_IN_SELECTED = "sign_in_selected"
    STAY_SIGNED_OUT_SELECTED = "stay_signed_out_selected"
    REPL_EXECUTION_POLICY_DECISION = "repl_execution_policy_decision"
    INSTALL_DETECTED = "install_detected"
    USER_ID_LOAD_FAILED = "user_id_load_failed"
    SENTRY_INIT_SKIPPED = "sentry_init_skipped"

    # Onboarding
    ONBOARD_STARTED = "onboard_started"
    ONBOARD_COMPLETED = "onboard_completed"
    ONBOARD_FAILED = "onboard_failed"

    # Integrations
    INTEGRATION_SETUP_STARTED = "integration_setup_started"
    INTEGRATION_SETUP_COMPLETED = "integration_setup_completed"
    INTEGRATION_REMOVED = "integration_removed"
    INTEGRATION_VERIFIED = "integration_verified"
    INTEGRATIONS_LISTED = "integrations_listed"

    # Interactive terminal analytics
    TERMINAL_ACTIONS_PLANNED = "terminal_actions_planned"
    TERMINAL_ACTIONS_EXECUTED = "terminal_actions_executed"
    TERMINAL_TURN_SUMMARIZED = "terminal_turn_summarized"
    REACT_TURN_COMPLETED = "react_turn_completed"
    AI_GENERATION = "$ai_generation"
    AGENT_TOOL_CALL_COMPLETED = "agent_tool_call_completed"
    ASK_USER_PROMPT_RENDERED = "ask_user_prompt_rendered"
    ASK_USER_PROMPT_ANSWERED = "ask_user_prompt_answered"
    ASK_USER_PROMPT_DISMISSED = "ask_user_prompt_dismissed"
    INTERACTIVE_SHELL_RENDERED = "interactive_shell_rendered"
    BROWSER_OPEN_REQUESTED = "browser_open_requested"
    SKILL_EXECUTED = "skill_executed"
    OPENSRE_COMMIT_CREATED = "opensre_commit_created"
    OPENSRE_CI_EPOCH_RESOLVED = "opensre_ci_epoch_resolved"

    # Gateway chat turns (Slack / Telegram) — usage sessions, not inventory
    GATEWAY_TURN_STARTED = "gateway_turn_started"
    GATEWAY_TURN_COMPLETED = "gateway_turn_completed"
    GATEWAY_TURN_FAILED = "gateway_turn_failed"

    # Update
    UPDATE_STARTED = "update_started"
    UPDATE_COMPLETED = "update_completed"
    UPDATE_FAILED = "update_failed"

    # Local agent monitoring (Monitor Local Agents feature)
    AGENT_SECRET_DETECTED = "agent_secret_detected"
    AGENT_KILLED = "agent_killed"
    AGENT_KILL_FAILED = "agent_kill_failed"

    # Scheduled deliveries
    SCHEDULED_TASK_STARTED = "scheduled_task_started"
    SCHEDULED_TASK_COMPLETED = "scheduled_task_completed"
    SCHEDULED_TASK_FAILED = "scheduled_task_failed"

    # Suggested loops (interactive-shell startup picker shown when no
    # scheduled tasks are configured)
    LOOP_SUGGESTION_PROMPTED = "loop_suggestion_prompted"
    LOOP_SUGGESTION_SELECTED = "loop_suggestion_selected"
    LOOP_SUGGESTION_SKIPPED = "loop_suggestion_skipped"

    # Onboarding demo picker (interactive-shell startup, first experience)
    ONBOARDING_DEMO_PROMPTED = "onboarding_demo_prompted"
    ONBOARDING_DEMO_SELECTED = "onboarding_demo_selected"
    ONBOARDING_DEMO_SKIPPED = "onboarding_demo_skipped"
