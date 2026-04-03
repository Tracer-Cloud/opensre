"""CLI analytics helpers."""

from __future__ import annotations

import os
from collections.abc import Mapping

from app.analytics.events import Event
from app.analytics.provider import Properties, get_analytics


def _string_value(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _mapping_value(mapping: Mapping[str, object], key: str) -> str | None:
    return _string_value(mapping.get(key))


def _onboard_completed_properties(config: Mapping[str, object]) -> Properties:
    properties: Properties = {}

    wizard_obj = config.get("wizard")
    if isinstance(wizard_obj, Mapping):
        wizard_mode = _mapping_value(wizard_obj, "mode")
        configured_target = _mapping_value(wizard_obj, "configured_target")
        if wizard_mode is not None:
            properties["wizard_mode"] = wizard_mode
        if configured_target is not None:
            properties["configured_target"] = configured_target

    targets_obj = config.get("targets")
    if isinstance(targets_obj, Mapping):
        local_obj = targets_obj.get("local")
        if isinstance(local_obj, Mapping):
            provider = _mapping_value(local_obj, "provider")
            model = _mapping_value(local_obj, "model")
            if provider is not None:
                properties["provider"] = provider
            if model is not None:
                properties["model"] = model

    return properties


def _investigation_started_properties(
    *,
    input_path: str | None,
    input_json: str | None,
    interactive: bool,
) -> Properties:
    properties: Properties = {
        "has_input_file": input_path is not None,
        "has_inline_json": input_json is not None,
        "interactive": interactive,
    }
    llm_provider = _string_value(os.getenv("LLM_PROVIDER"))
    llm_model = _string_value(os.getenv("ANTHROPIC_MODEL")) or _string_value(os.getenv("OPENAI_MODEL"))
    if llm_provider is not None:
        properties["llm_provider"] = llm_provider
    if llm_model is not None:
        properties["llm_model"] = llm_model
    return properties


def capture_cli_invoked() -> None:
    get_analytics().capture(Event.CLI_INVOKED)


def capture_onboard_started() -> None:
    get_analytics().capture(Event.ONBOARD_STARTED)


def capture_onboard_completed(config: Mapping[str, object]) -> None:
    get_analytics().capture(Event.ONBOARD_COMPLETED, _onboard_completed_properties(config))


def capture_onboard_failed() -> None:
    get_analytics().capture(Event.ONBOARD_FAILED)


def capture_investigation_started(
    *,
    input_path: str | None,
    input_json: str | None,
    interactive: bool,
) -> None:
    get_analytics().capture(
        Event.INVESTIGATION_STARTED,
        _investigation_started_properties(
            input_path=input_path,
            input_json=input_json,
            interactive=interactive,
        ),
    )


def capture_investigation_completed() -> None:
    get_analytics().capture(Event.INVESTIGATION_COMPLETED)


def capture_investigation_failed() -> None:
    get_analytics().capture(Event.INVESTIGATION_FAILED)


def capture_integration_setup_started(service: str) -> None:
    get_analytics().capture(Event.INTEGRATION_SETUP_STARTED, {"service": service})


def capture_integration_setup_completed(service: str) -> None:
    get_analytics().capture(Event.INTEGRATION_SETUP_COMPLETED, {"service": service})


def capture_integrations_listed() -> None:
    get_analytics().capture(Event.INTEGRATIONS_LISTED)


def capture_integration_removed(service: str) -> None:
    get_analytics().capture(Event.INTEGRATION_REMOVED, {"service": service})


def capture_integration_verified(service: str) -> None:
    get_analytics().capture(Event.INTEGRATION_VERIFIED, {"service": service})


def capture_integration_added(service: str) -> None:
    get_analytics().capture(Event.INTEGRATION_ADDED, {"service": service})


def capture_tests_picker_opened() -> None:
    get_analytics().capture(Event.TESTS_PICKER_OPENED)


def capture_test_synthetic_started(scenario: str, *, mock_grafana: bool) -> None:
    get_analytics().capture(
        Event.TEST_SYNTHETIC_STARTED,
        {"scenario": scenario, "mock_grafana": mock_grafana},
    )


def capture_tests_listed(category: str, *, search: bool) -> None:
    get_analytics().capture(Event.TESTS_LISTED, {"category": category, "search": search})


def capture_test_run_started(test_id: str, *, dry_run: bool) -> None:
    get_analytics().capture(Event.TEST_RUN_STARTED, {"test_id": test_id, "dry_run": dry_run})


def capture_update_started(*, check_only: bool) -> None:
    get_analytics().capture(Event.UPDATE_STARTED, {"check_only": check_only})


def capture_update_completed() -> None:
    get_analytics().capture(Event.UPDATE_COMPLETED)


def capture_update_failed() -> None:
    get_analytics().capture(Event.UPDATE_FAILED)
