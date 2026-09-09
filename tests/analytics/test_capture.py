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


def test_eval_and_terminal_kpi_queries_cover_core_metrics() -> None:
    expected_keys = {
        "terminal_action_execution_success_rate",
        "terminal_fallback_rate",
    }
    assert expected_keys.issubset(capture.EVAL_AND_TERMINAL_KPI_QUERIES.keys())
    for query in capture.EVAL_AND_TERMINAL_KPI_QUERIES.values():
        assert "FROM events" in query
