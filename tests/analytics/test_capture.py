from __future__ import annotations

import pytest

from infrastructure.analytics import (
    capture,
    event_properties,
    github_identity,
)
from infrastructure.analytics.events import Event


class _StubAnalytics:
    def __init__(self) -> None:
        self.events: list[tuple[Event, dict[str, object] | None]] = []
        self.identified: list[dict[str, object]] = []
        self.persistent_properties: dict[str, object] = {}
        self.destination_refreshes = 0

    def capture(self, event: Event, properties: dict[str, object] | None = None) -> None:
        self.events.append((event, properties))

    def identify(self, set_properties: dict[str, object]) -> None:
        self.identified.append(set_properties)

    def set_persistent_property(self, key: str, value: object) -> None:
        self.persistent_properties[key] = value

    def refresh_destination(self) -> None:
        self.destination_refreshes += 1


def test_capture_cli_invoked_uses_safe_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(capture, "get_analytics", lambda: stub)

    capture.capture_cli_invoked({"command_path": "opensre version"})

    assert stub.events == [
        (Event.CLI_INVOKED, {"command_path": "opensre version"}),
    ]


def test_capture_cli_invoked_reports_analytics_failures_to_sentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_errors: list[BaseException] = []
    expected_error = RuntimeError("analytics unavailable")

    def raise_error() -> _StubAnalytics:
        raise expected_error

    monkeypatch.setattr(capture, "get_analytics", raise_error)
    monkeypatch.setattr(capture, "capture_exception", captured_errors.append)

    capture.capture_cli_invoked()

    assert captured_errors == [expected_error]


def test_capture_account_authenticated_refreshes_credentials_before_link_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _OrderCheckingAnalytics(_StubAnalytics):
        def capture(self, event: Event, properties: dict[str, object] | None = None) -> None:
            assert self.destination_refreshes == 1
            super().capture(event, properties)

    stub = _OrderCheckingAnalytics()
    monkeypatch.setattr(capture, "get_analytics", lambda: stub)

    capture.capture_account_authenticated()

    assert stub.destination_refreshes == 1
    assert stub.events == [(Event.ACCOUNT_AUTHENTICATED, None)]


def test_identify_github_username_sets_person_property(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(github_identity, "get_analytics", lambda: stub)

    github_identity.identify_github_username("octocat")

    assert stub.identified == [{"github_username": "octocat"}]
    assert stub.persistent_properties == {"github_username": "octocat"}


def test_identify_github_username_noop_on_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(github_identity, "get_analytics", lambda: stub)

    github_identity.identify_github_username("")

    assert stub.identified == []


def test_identify_saved_github_username_reads_store(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(github_identity, "get_analytics", lambda: stub)
    # Patch the package API surface (what production imports). Patching only
    # ``integrations.github.identity`` misses when another test has bound the
    # name on ``integrations.github`` and shadowed ``__getattr__``.
    monkeypatch.setattr("integrations.github.saved_github_username", lambda: "octocat")

    github_identity.identify_saved_github_username()

    assert stub.identified == [{"github_username": "octocat"}]
    assert stub.persistent_properties == {"github_username": "octocat"}


def test_identify_saved_github_username_noop_when_store_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(github_identity, "get_analytics", lambda: stub)
    monkeypatch.setattr("integrations.github.saved_github_username", lambda: "")

    github_identity.identify_saved_github_username()

    assert stub.identified == []


def test_identify_github_username_reports_failures_to_sentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_errors: list[BaseException] = []
    expected_error = RuntimeError("analytics unavailable")

    def raise_error() -> _StubAnalytics:
        raise expected_error

    monkeypatch.setattr(github_identity, "get_analytics", raise_error)
    monkeypatch.setattr(github_identity, "capture_exception", captured_errors.append)

    github_identity.identify_github_username("octocat")

    assert captured_errors == [expected_error]


def test_build_cli_invoked_properties_includes_full_command_path() -> None:
    properties = event_properties.build_cli_invoked_properties(
        entrypoint="opensre",
        command_parts=["remote", "ops", "status"],
        debug=True,
    )

    assert properties == {
        "entrypoint": "opensre",
        "command_path": "opensre remote ops status",
        "command_family": "remote",
        "json_output": False,
        "verbose": False,
        "debug": True,
        "yes": False,
        "interactive": True,
        "subcommand": "ops",
        "command_leaf": "status",
    }


def test_build_cli_invoked_properties_handles_root_invocation() -> None:
    properties = event_properties.build_cli_invoked_properties(
        entrypoint="opensre",
        command_parts=[],
    )

    assert properties["command_path"] == "opensre"
    assert properties["command_family"] == "root"
    assert "subcommand" not in properties
    assert "command_leaf" not in properties


def test_build_install_detected_properties_keeps_installer_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSRE_INSTALL_SOURCE", "posix_installer")
    monkeypatch.setenv("OPENSRE_INSTALL_CHANNEL", "release")
    monkeypatch.setenv("OPENSRE_INSTALL_VERSION", "2026.9.14")
    monkeypatch.setattr(event_properties.sys, "frozen", True, raising=False)

    properties = event_properties.build_install_detected_properties(entrypoint="opensre")

    assert properties == {
        "entrypoint": "opensre",
        "install_source": "posix_installer",
        "distribution": "frozen_binary",
        "install_channel": "release",
        "installed_version": "2026.9.14",
    }


def test_build_install_detected_properties_redacts_secret_shaped_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENSRE_INSTALL_SOURCE", "ghp_abcdefghijklmnopqrstuvwxyz1234567890")
    monkeypatch.setenv("OPENSRE_INSTALL_CHANNEL", "release")
    monkeypatch.setenv("OPENSRE_INSTALL_VERSION", "ghp_abcdefghijklmnopqrstuvwxyz1234567890")

    properties = event_properties.build_install_detected_properties(entrypoint="opensre")

    assert "ghp_" not in str(properties)
    assert properties["install_source"] == "[REDACTED:github_pat]"
    assert properties["installed_version"] == "[REDACTED:github_pat]"


def test_capture_update_helpers_emit_expected_events(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(capture, "get_analytics", lambda: stub)

    capture.capture_update_started(check_only=True)
    capture.capture_update_completed(check_only=False, updated=True)
    capture.capture_update_failed(check_only=False, reason="RuntimeError")

    assert stub.events == [
        (Event.UPDATE_STARTED, {"check_only": True}),
        (Event.UPDATE_COMPLETED, {"check_only": False, "updated": True}),
        (Event.UPDATE_FAILED, {"check_only": False, "reason": "RuntimeError"}),
    ]


def test_capture_terminal_metrics_emit_expected_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(capture, "get_analytics", lambda: stub)

    capture.capture_terminal_actions_planned(planned_count=3, has_unhandled_clause=True)
    capture.capture_terminal_actions_executed(
        planned_count=3,
        executed_count=2,
        executed_success_count=1,
    )
    capture.capture_terminal_turn_summarized(
        planned_count=3,
        executed_count=2,
        executed_success_count=1,
        fallback_to_llm=True,
        session_turn_index=8,
        session_fallback_count=3,
        session_action_success_percent=75.0,
        session_fallback_rate_percent=37.5,
    )

    for event, properties in stub.events:
        assert properties is not None
        required = capture.EVAL_AND_TERMINAL_EVENT_CONTRACT.get(event)
        if required is None:
            continue
        assert required.issubset(properties.keys())


def test_capture_ask_user_events_link_redacted_prompt_and_selected_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(capture, "get_analytics", lambda: stub)
    questions = [
        {
            "label": "Access",
            "title": "Use token ghp_abcdefghijklmnopqrstuvwxyz1234567890?",
            "options": ["Read only", "Admin"],
            "multi_select": False,
        }
    ]

    capture.capture_ask_user_prompt_rendered(
        interaction_id="prompt-1",
        questions=questions,
        render_mode="picker",
        allow_custom=True,
        has_command_options=False,
        skill_name="triage",
    )
    capture.capture_ask_user_prompt_answered(
        interaction_id="prompt-1",
        selected_option_indices=((0,),),
        custom_answers=(None,),
        disposition="agent_answer",
        skill_name="triage",
    )

    rendered = stub.events[0][1]
    answered = stub.events[1][1]
    assert rendered is not None and answered is not None
    assert stub.events[0][0] is Event.ASK_USER_PROMPT_RENDERED
    assert stub.events[1][0] is Event.ASK_USER_PROMPT_ANSWERED
    assert "ghp_" not in str(rendered["questions"])
    assert rendered["interaction_id"] == answered["interaction_id"] == "prompt-1"
    assert answered["answers"] == [
        {
            "question_index": 0,
            "selected_option_indices": [0],
            "custom": False,
        }
    ]
    assert "answer" not in answered["answers"][0]


def test_capture_ask_user_answered_keeps_bounded_custom_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(capture, "get_analytics", lambda: stub)
    capture.capture_ask_user_prompt_answered(
        interaction_id="prompt-2",
        selected_option_indices=((1,),),
        custom_answers=("Use token ghp_abcdefghijklmnopqrstuvwxyz1234567890",),
        disposition="agent_answer",
        skill_name=None,
    )

    answered = stub.events[0][1]
    assert answered is not None
    detail = answered["answers"][0]
    assert detail["custom"] is True
    assert detail["selected_option_indices"] == [1]
    assert "ghp_" not in str(detail["answer"])
    assert "[REDACTED:github_pat]" in str(detail["answer"])


def test_eval_and_terminal_kpi_queries_cover_core_metrics() -> None:
    expected_keys = {
        "terminal_action_execution_success_rate",
        "terminal_fallback_rate",
    }
    assert expected_keys.issubset(capture.EVAL_AND_TERMINAL_KPI_QUERIES.keys())
    for query in capture.EVAL_AND_TERMINAL_KPI_QUERIES.values():
        assert "FROM events" in query
