"""Analytics event definitions."""

from __future__ import annotations

from enum import StrEnum


class Event(StrEnum):
    # Lifecycle
    CLI_INVOKED = "cli_invoked"
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
