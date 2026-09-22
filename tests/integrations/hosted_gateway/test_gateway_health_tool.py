"""Tests for the check_hosted_gateway tool: what the user is told in each state."""

from __future__ import annotations

from types import TracebackType
from typing import Any

import pytest

from integrations.hosted_gateway import GatewayHealth, HostedGatewayError
from integrations.hosted_gateway.tools import gateway_health, results
from integrations.hosted_gateway.tools.gateway_health import check_hosted_gateway
from tools.registry import clear_tool_registry_cache, get_registered_tool_map


class _Client:
    app_url = "https://app.test"

    def __init__(self, outcome: GatewayHealth | HostedGatewayError) -> None:
        self._outcome = outcome

    def __enter__(self) -> _Client:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def health(self) -> GatewayHealth:
        if isinstance(self._outcome, HostedGatewayError):
            raise self._outcome
        return self._outcome


def _signed_in_with(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> None:
    def from_account() -> _Client:
        return _Client(outcome)

    monkeypatch.setattr(gateway_health.HostedGatewayClient, "from_account", from_account)


def test_the_tool_is_registered_read_only_and_takes_no_identifier() -> None:
    # Arrange
    clear_tool_registry_cache()

    # Act
    tool = get_registered_tool_map()["check_hosted_gateway"]

    # Assert: there is no argument through which another organization could be named.
    assert tool.side_effect_level == "read_only"
    assert tool.input_schema["properties"] == {}
    assert tool.input_schema["additionalProperties"] is False


def test_a_running_gateway_is_reported_as_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    _signed_in_with(
        monkeypatch,
        GatewayHealth(True, True, gateway_id="org-gateway", actual_state="running"),
    )

    # Act
    out = check_hosted_gateway()

    # Assert
    assert out["success"] is True and out["healthy"] is True and out["provisioned"] is True
    assert out["response_text"] == "Your organization's hosted gateway org-gateway is running."


@pytest.mark.parametrize(
    ("health", "expected"),
    [
        (
            GatewayHealth(False, False),
            "has no hosted gateway yet. An organization admin can set it up at "
            "https://app.test/settings/agent-backend.",
        ),
        (
            GatewayHealth(True, False, actual_state="provisioning"),
            "is starting; check again in a minute.",
        ),
        (
            GatewayHealth(True, False, actual_state="failed", last_error_code="image_pull"),
            "is failed. Last error: image_pull. See https://app.test/settings/agent-backend.",
        ),
    ],
)
def test_every_other_state_says_what_it_is_and_where_to_go(
    monkeypatch: pytest.MonkeyPatch, health: GatewayHealth, expected: str
) -> None:
    # Arrange
    _signed_in_with(monkeypatch, health)

    # Act
    out = check_hosted_gateway()

    # Assert: the app answered, so the call succeeded even though the gateway is not serving.
    assert out["success"] is True and out["healthy"] is False
    assert out["response_text"].endswith(expected)


def test_not_signed_in_tells_the_user_to_sign_in_and_is_not_an_incident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    reported: list[BaseException] = []
    monkeypatch.setattr(results, "report_run_error", lambda exc, **_kw: reported.append(exc))

    def from_account() -> _Client:
        raise HostedGatewayError("not_signed_in")

    monkeypatch.setattr(gateway_health.HostedGatewayClient, "from_account", from_account)

    # Act
    out = check_hosted_gateway()

    # Assert
    assert out["success"] is False and out["signed_in"] is False
    assert out["error_kind"] == "not_signed_in"
    assert "opensre account login" in out["response_text"]
    assert reported == []


def test_an_unreachable_app_is_reported_once_and_named_by_code_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    reported: list[BaseException] = []
    monkeypatch.setattr(results, "report_run_error", lambda exc, **_kw: reported.append(exc))
    _signed_in_with(monkeypatch, HostedGatewayError("unreachable"))

    # Act
    out = check_hosted_gateway()

    # Assert
    assert out["success"] is False and out["error_kind"] == "unreachable"
    assert out["response_text"] == "The OpenSRE app could not do that (unreachable)."
    assert len(reported) == 1


def test_the_hosted_gateway_tools_are_withheld_where_hosted_gateway_is_disabled() -> None:
    # Arrange: a session that withholds the capability, as the gateway itself does
    from integrations.hosted_gateway.tools.results import hosted_gateway_available

    withheld = {"_action_session": {"available_capabilities": {"hosted_gateway": ()}}}
    laptop = {"_action_session": {"available_capabilities": {}}}

    # Act
    on_gateway = hosted_gateway_available(withheld)
    on_laptop = hosted_gateway_available(laptop)

    # Assert
    assert on_gateway is False and on_laptop is True
