"""Tests for starting and stopping the hosted gateway: what the app refuses, and what the user is told."""

from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus
from typing import Any

import httpx
import pytest

from integrations.hosted_gateway import (
    ERR_NOT_PROVISIONED,
    GatewayHealth,
    HostedGatewayClient,
    HostedGatewayError,
)
from integrations.hosted_gateway.tools import gateway_lifecycle, results
from integrations.hosted_gateway.tools.gateway_health import check_hosted_gateway
from integrations.hosted_gateway.tools.gateway_lifecycle import (
    start_hosted_gateway,
    stop_hosted_gateway,
)
from tools.registry import clear_tool_registry_cache, get_registered_tool_map

_TOKEN = "osre_pat_secret_value"


def _client(handler: httpx.MockTransport) -> HostedGatewayClient:
    return HostedGatewayClient(app_url="https://app.test", token=_TOKEN, transport=handler)


@pytest.mark.parametrize(
    ("call", "path"),
    [("start", "/api/agent-backend/gateway/start"), ("stop", "/api/agent-backend/gateway/stop")],
)
def test_start_and_stop_post_with_the_token_only_and_name_no_organization(
    call: str, path: str
) -> None:
    # Arrange
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"provisioned": True, "healthy": False, "actual_state": "provisioning"}
        )

    # Act
    with _client(httpx.MockTransport(answer)) as client:
        health = getattr(client, call)()

    # Assert
    request = seen[0]
    assert (request.method, request.url.path) == ("POST", path)
    assert request.headers["authorization"] == f"Bearer {_TOKEN}"
    assert request.url.query == b"" and request.content == b""
    assert health.actual_state == "provisioning"


def test_an_unprovisioned_organization_is_refused_with_a_stable_code() -> None:
    # Arrange
    client = _client(
        httpx.MockTransport(
            lambda _r: httpx.Response(HTTPStatus.CONFLICT, json={"error": ERR_NOT_PROVISIONED})
        )
    )

    # Act
    with pytest.raises(HostedGatewayError) as excinfo:
        client.stop()

    # Assert
    assert excinfo.value.code == ERR_NOT_PROVISIONED
    assert _TOKEN not in str(excinfo.value)


def test_both_tools_change_shared_state_and_take_no_identifier() -> None:
    # Arrange
    clear_tool_registry_cache()

    # Act
    tools = get_registered_tool_map()

    # Assert
    for name in ("start_hosted_gateway", "stop_hosted_gateway"):
        tool = tools[name]
        assert tool.side_effect_level == "mutating"
        assert tool.requires_approval is False
        assert tool.input_schema["properties"] == {}
        assert tool.input_schema["additionalProperties"] is False


class _Client:
    """Start and stop answer with ``outcome``; health answers with ``health_outcome`` when given."""

    app_url = "https://app.test"
    health_outcome: GatewayHealth | HostedGatewayError | None = None
    calls: list[str] = []

    def __init__(self, outcome: GatewayHealth | HostedGatewayError) -> None:
        self._outcome = outcome

    def __enter__(self) -> _Client:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def _answer(self) -> GatewayHealth:
        if isinstance(self._outcome, HostedGatewayError):
            raise self._outcome
        return self._outcome

    def health(self) -> GatewayHealth:
        type(self).calls.append("health")
        outcome = type(self).health_outcome
        if outcome is None:
            return self._answer()
        if isinstance(outcome, HostedGatewayError):
            raise outcome
        return outcome

    def start(self) -> GatewayHealth:
        type(self).calls.append("start")
        return self._answer()

    def stop(self) -> GatewayHealth:
        type(self).calls.append("stop")
        return self._answer()


def _signed_in_with(
    monkeypatch: pytest.MonkeyPatch, outcome: GatewayHealth | HostedGatewayError
) -> None:
    def from_account() -> _Client:
        return _Client(outcome)

    monkeypatch.setattr(gateway_lifecycle.HostedGatewayClient, "from_account", from_account)


def test_stop_says_the_gateway_is_stopped_and_that_nothing_was_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    _signed_in_with(
        monkeypatch, GatewayHealth(True, False, gateway_id="org-gateway", actual_state="stopped")
    )

    # Act
    out = stop_hosted_gateway()

    # Assert
    assert out["success"] is True and out["healthy"] is False
    assert out["response_text"] == (
        "Your organization's hosted gateway org-gateway is stopped. Its configuration is "
        "kept; start it again when you need it."
    )


def test_start_reports_that_the_gateway_is_still_coming_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    _signed_in_with(
        monkeypatch,
        GatewayHealth(True, False, gateway_id="org-gateway", actual_state="provisioning"),
    )
    _Client.calls = []

    # Act
    out = start_hosted_gateway()

    # Assert: not running, so a start was requested and the reply says it is coming up
    assert _Client.calls == ["health", "start"]
    assert out["success"] is True and out["actual_state"] == "provisioning"
    assert "is provisioning now. Check it again in a minute." in out["response_text"]


def test_starting_a_running_gateway_still_requests_the_start_and_says_it_was_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The start request always goes to the app (its refusals apply); the reply is accurate."""
    # Arrange
    running = GatewayHealth(True, True, gateway_id="org-gateway", actual_state="running")
    _signed_in_with(monkeypatch, running)
    _Client.health_outcome = None
    _Client.calls = []

    # Act
    out = start_hosted_gateway()

    # Assert
    assert _Client.calls == ["health", "start"]
    assert out["success"] is True and out["healthy"] is True
    assert out["response_text"].endswith("is already running; nothing to start.")


def test_a_failed_health_read_does_not_stop_a_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """The user asked for a start; a health timeout must not leave a stopped gateway stopped."""
    # Arrange: health errors, the start itself works
    _signed_in_with(
        monkeypatch,
        GatewayHealth(True, False, gateway_id="org-gateway", actual_state="provisioning"),
    )
    _Client.health_outcome = HostedGatewayError(ERR_NOT_PROVISIONED, HTTPStatus.SERVICE_UNAVAILABLE)
    _Client.calls = []

    # Act
    out = start_hosted_gateway()

    # Assert
    _Client.health_outcome = None
    assert _Client.calls == ["health", "start"]
    assert out["success"] is True and "is provisioning now" in out["response_text"]


def test_a_failed_start_of_a_running_gateway_is_not_reported_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy gateway must not turn a failed start into a successful-looking reply."""
    # Arrange: health reads fine, the start itself fails at the control plane
    monkeypatch.setattr(results, "report_run_error", lambda _exc, **_kw: None)
    _signed_in_with(monkeypatch, HostedGatewayError("http_502", HTTPStatus.BAD_GATEWAY))
    _Client.health_outcome = GatewayHealth(
        True, True, gateway_id="org-gateway", actual_state="running"
    )
    _Client.calls = []

    # Act
    out = start_hosted_gateway()

    # Assert
    _Client.health_outcome = None
    assert _Client.calls == ["health", "start"]
    assert out["success"] is False and out["error_kind"] == "http_502"


def _record_gateway_events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        gateway_lifecycle,
        "capture_hosted_gateway_started",
        lambda **kw: events.append(("started", kw)),
    )
    monkeypatch.setattr(
        gateway_lifecycle,
        "capture_hosted_gateway_healthy",
        lambda **kw: events.append(("healthy", kw)),
    )
    return events


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (
            GatewayHealth(True, False, gateway_id="org-gateway", actual_state="stopped"),
            GatewayHealth(True, False, gateway_id="org-gateway", actual_state="provisioning"),
            [
                (
                    "started",
                    {
                        "gateway_id": "org-gateway",
                        "actual_state": "provisioning",
                        "already_running": False,
                    },
                )
            ],
        ),
        (
            GatewayHealth(True, True, gateway_id="org-gateway", actual_state="running"),
            GatewayHealth(True, True, gateway_id="org-gateway", actual_state="running"),
            [
                (
                    "started",
                    {
                        "gateway_id": "org-gateway",
                        "actual_state": "running",
                        "already_running": True,
                    },
                ),
                ("healthy", {"gateway_id": "org-gateway", "tool_name": "start_hosted_gateway"}),
            ],
        ),
    ],
    ids=["coming-up", "already-running"],
)
def test_an_accepted_start_is_recorded_and_healthy_only_once_it_serves(
    monkeypatch: pytest.MonkeyPatch,
    before: GatewayHealth,
    after: GatewayHealth,
    expected: list[tuple[str, dict[str, object]]],
) -> None:
    # Arrange
    events = _record_gateway_events(monkeypatch)
    _signed_in_with(monkeypatch, after)
    monkeypatch.setattr(_Client, "health_outcome", before)

    # Act
    start_hosted_gateway()

    # Assert
    assert events == expected


@pytest.mark.parametrize(
    ("call", "outcome"),
    [
        (start_hosted_gateway, HostedGatewayError(ERR_NOT_PROVISIONED, HTTPStatus.CONFLICT)),
        (stop_hosted_gateway, GatewayHealth(True, False, actual_state="stopped")),
    ],
    ids=["refused-start", "stop"],
)
def test_a_refused_start_and_a_stop_record_no_gateway_milestone(
    monkeypatch: pytest.MonkeyPatch,
    call: Callable[[], dict[str, Any]],
    outcome: GatewayHealth | HostedGatewayError,
) -> None:
    # Arrange
    events = _record_gateway_events(monkeypatch)
    monkeypatch.setattr(results, "report_run_error", lambda _exc, **_kw: None)
    _signed_in_with(monkeypatch, outcome)

    # Act
    call()

    # Assert
    assert events == []


@pytest.mark.parametrize("call", [start_hosted_gateway, stop_hosted_gateway], ids=["start", "stop"])
def test_an_unprovisioned_organization_is_sent_to_the_admin_page_and_it_is_not_an_incident(
    monkeypatch: pytest.MonkeyPatch, call: Callable[[], dict[str, Any]]
) -> None:
    """A refused start or stop names the admin page in the words check_hosted_gateway uses."""
    # Arrange: the app refuses the lifecycle call and reads the gateway as not provisioned
    reported: list[BaseException] = []
    monkeypatch.setattr(results, "report_run_error", lambda exc, **_kw: reported.append(exc))
    _signed_in_with(monkeypatch, HostedGatewayError(ERR_NOT_PROVISIONED, HTTPStatus.CONFLICT))
    monkeypatch.setattr(_Client, "health_outcome", GatewayHealth(False, False))

    # Act
    out = call()
    checked = check_hosted_gateway()

    # Assert
    assert out["success"] is False and out["error_kind"] == ERR_NOT_PROVISIONED
    assert "https://app.test/settings/agent-backend" in out["response_text"]
    assert out["response_text"] == checked["response_text"]
    assert reported == []
