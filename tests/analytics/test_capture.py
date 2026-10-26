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
        self.events: list[tuple[str, dict[str, object] | None]] = []
        self.identified: list[dict[str, object]] = []
        self.persistent_properties: dict[str, object] = {}
        self.destination_refreshes = 0

    def capture(self, event: str, properties: dict[str, object] | None = None) -> None:
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

    capture.capture_cli_invoked({"command_path": "opensre health"}, ["health"])

    assert stub.events == [
        ("cli_command_opensre_health", {"command_path": "opensre health"}),
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
        def capture(self, event: str, properties: dict[str, object] | None = None) -> None:
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


def test_build_cli_invoked_properties_includes_full_command_path(monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr("sys.stdout", SimpleNamespace(isatty=lambda: False))
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
        "stdin_is_tty": False,
        "stdout_is_tty": False,
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
    monkeypatch.delenv("OPENSRE_INSTALL_ORIGIN", raising=False)
    monkeypatch.setenv("OPENSRE_INSTALL_SOURCE", "posix_installer")
    monkeypatch.setenv("OPENSRE_INSTALL_CHANNEL", "release")
    monkeypatch.setenv("OPENSRE_INSTALL_VERSION", "2026.9.14")
    monkeypatch.setattr(event_properties, "detect_distribution", lambda: "frozen_binary")

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
        required = capture.EVAL_AND_TERMINAL_EVENT_CONTRACT.get(Event(event))
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
    answers = answered["answers"]
    assert isinstance(answers, list)
    detail = answers[0]
    assert detail["custom"] is True
    assert detail["selected_option_indices"] == [1]
    assert "ghp_" not in str(detail["answer"])
    assert "[REDACTED:github_pat]" in str(detail["answer"])


def test_capture_ask_user_dismissed_names_the_closing_key_only_when_known(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _StubAnalytics()
    monkeypatch.setattr(capture, "get_analytics", lambda: stub)

    capture.capture_ask_user_prompt_dismissed(
        interaction_id="prompt-3", reason="cancelled", skill_name=None, dismiss_key="esc"
    )
    capture.capture_ask_user_prompt_dismissed(
        interaction_id="prompt-4", reason="cancelled", skill_name=None
    )

    assert [event for event, _properties in stub.events] == [
        Event.ASK_USER_PROMPT_DISMISSED,
        Event.ASK_USER_PROMPT_DISMISSED,
    ]
    assert stub.events[0][1] == {
        "interaction_id": "prompt-3",
        "reason": "cancelled",
        "dismiss_key": "esc",
    }
    assert stub.events[1][1] == {"interaction_id": "prompt-4", "reason": "cancelled"}


def test_remote_ci_repair_events_name_the_run_and_omit_an_unknown_pull_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    stub = _StubAnalytics()
    monkeypatch.setattr(capture, "get_analytics", lambda: stub)
    run_id, repository = "a" * 12, "alice/opensre-ci-demo"

    # Act: scheduled before the demo PR exists, then repaired on PR 7
    capture.capture_remote_ci_monitoring_started(
        repair_run_id=run_id, repository=repository, pr_number=0, demo=True
    )
    capture.capture_test_ci_failure_triggered(
        repair_run_id=run_id, repository=repository, pr_number=7, demo=True, remote=True
    )
    capture.capture_remote_ci_failure_detected(
        repair_run_id=run_id, repository=repository, pr_number=7, demo=True
    )
    capture.capture_remote_ci_repair_succeeded(
        repair_run_id=run_id,
        repository=repository,
        pr_number=7,
        demo=True,
        attempts=2,
        duration_ms=1234.6,
    )

    # Assert
    run = {"repair_run_id": run_id, "repository": repository, "demo": True}
    on_pr = {**run, "pr_number": 7}
    assert stub.events == [
        (Event.REMOTE_CI_MONITORING_STARTED, run),
        (Event.TEST_CI_FAILURE_TRIGGERED, {**on_pr, "remote": True}),
        (Event.REMOTE_CI_FAILURE_DETECTED, on_pr),
        (Event.REMOTE_CI_REPAIR_SUCCEEDED, {**on_pr, "attempts": 2, "duration_ms": 1235}),
    ]


def test_hosted_gateway_events_name_the_gateway_and_what_observed_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    stub = _StubAnalytics()
    monkeypatch.setattr(capture, "get_analytics", lambda: stub)

    # Act
    capture.capture_hosted_gateway_started(
        gateway_id="org-gateway", actual_state="provisioning", already_running=False
    )
    capture.capture_hosted_gateway_healthy(
        gateway_id="org-gateway", tool_name="check_hosted_gateway"
    )

    # Assert
    assert stub.events == [
        (
            Event.HOSTED_GATEWAY_STARTED,
            {
                "gateway_id": "org-gateway",
                "actual_state": "provisioning",
                "already_running": False,
            },
        ),
        (
            Event.HOSTED_GATEWAY_HEALTHY,
            {"gateway_id": "org-gateway", "tool_name": "check_hosted_gateway"},
        ),
    ]


def test_eval_and_terminal_kpi_queries_cover_core_metrics() -> None:
    expected_keys = {
        "terminal_action_execution_success_rate",
        "terminal_fallback_rate",
    }
    assert expected_keys.issubset(capture.EVAL_AND_TERMINAL_KPI_QUERIES.keys())
    for query in capture.EVAL_AND_TERMINAL_KPI_QUERIES.values():
        assert "FROM events" in query


@pytest.mark.parametrize(
    "origin", ["landing_page", "documentation", "github", "cicd", "", "main", "unsupported"]
)
def test_install_origin_is_allowlisted_and_independent_of_build_track(
    monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    monkeypatch.setenv("OPENSRE_INSTALL_ORIGIN", origin)
    monkeypatch.setenv("OPENSRE_INSTALL_CHANNEL", "release")
    properties = event_properties.build_install_detected_properties(entrypoint="opensre")
    assert properties["install_channel"] == "release"
    if origin in {"landing_page", "documentation", "github", "cicd"}:
        assert properties["install_origin"] == origin
    else:
        assert "install_origin" not in properties


def test_cli_auth_attempt_is_fresh_and_respects_telemetry_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from uuid import UUID

    stub = _StubAnalytics()
    monkeypatch.setattr(capture, "get_analytics", lambda: stub)
    for key in ("OPENSRE_NO_TELEMETRY", "OPENSRE_ANALYTICS_DISABLED", "DO_NOT_TRACK"):
        monkeypatch.delenv(key, raising=False)
    first = capture.begin_cli_auth_attempt()
    second = capture.begin_cli_auth_attempt()
    assert first is not None and second is not None
    assert UUID(first).version == 4
    assert first != second
    assert stub.events == [
        (Event.CLI_AUTH_STARTED, {"cli_auth_attempt_id": first}),
        (Event.CLI_AUTH_STARTED, {"cli_auth_attempt_id": second}),
    ]
    for key in ("OPENSRE_NO_TELEMETRY", "OPENSRE_ANALYTICS_DISABLED", "DO_NOT_TRACK"):
        monkeypatch.setenv(key, "1")
        assert capture.begin_cli_auth_attempt() is None
        monkeypatch.delenv(key)
    assert len(stub.events) == 2
