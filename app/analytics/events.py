"""Analytics event definitions."""

from __future__ import annotations

from enum import StrEnum


class Event(StrEnum):
    # Lifecycle
    CLI_INVOKED = "cli_invoked"
    INSTALL_DETECTED = "install_detected"

    # Onboarding
    ONBOARD_STARTED = "onboard_started"
    ONBOARD_COMPLETED = "onboard_completed"
    ONBOARD_FAILED = "onboard_failed"

    # Investigation
    INVESTIGATION_STARTED = "investigation_started"
    INVESTIGATION_COMPLETED = "investigation_completed"
    INVESTIGATION_FAILED = "investigation_failed"

    # Integrations
    INTEGRATION_SETUP_STARTED = "integration_setup_started"
    INTEGRATION_SETUP_COMPLETED = "integration_setup_completed"
    INTEGRATION_ADDED = "integration_added"
    INTEGRATION_REMOVED = "integration_removed"
    INTEGRATION_VERIFIED = "integration_verified"
    INTEGRATIONS_LISTED = "integrations_listed"

    # Tests
    TESTS_PICKER_OPENED = "tests_picker_opened"
    TESTS_LISTED = "tests_listed"
    TEST_RUN_STARTED = "test_run_started"
    TEST_SYNTHETIC_STARTED = "test_synthetic_started"

    # Update
    UPDATE_STARTED = "update_started"
    UPDATE_COMPLETED = "update_completed"
    UPDATE_FAILED = "update_failed"
