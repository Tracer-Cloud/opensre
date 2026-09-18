"""Tests for the hosted-gateway client: who the token is sent to, and what comes back."""

from __future__ import annotations

import httpx
import pytest

from integrations.hosted_gateway import (
    ERR_INSECURE_APP_URL,
    ERR_INVALID_RESPONSE,
    ERR_NOT_SIGNED_IN,
    ERR_NOT_SUPPORTED,
    ERR_UNAUTHORIZED,
    ERR_UNREACHABLE,
    GatewayHealth,
    HostedGatewayClient,
    HostedGatewayError,
)

_TOKEN = "osre_pat_secret_value"


def _client(handler: httpx.MockTransport, app_url: str = "https://app.test") -> HostedGatewayClient:
    return HostedGatewayClient(app_url=app_url, token=_TOKEN, transport=handler)


def test_health_sends_only_the_account_token_and_names_no_organization() -> None:
    # Arrange
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "provisioned": True,
                "healthy": True,
                "gateway_id": "260917-opensre-org_abc-gateway",
                "desired_state": "running",
                "actual_state": "running",
                "size_profile": "MEDIUM",
                "last_error_code": None,
                "updated_at": "2026-09-18T10:00:00Z",
            },
        )

    # Act
    with _client(httpx.MockTransport(answer)) as client:
        health = client.health()

    # Assert: the app maps the token to the organization; the request carries no identifier.
    request = seen[0]
    assert request.method == "GET"
    assert str(request.url) == "https://app.test/api/agent-backend/gateway/health"
    assert request.headers["authorization"] == f"Bearer {_TOKEN}"
    assert request.url.query == b"" and request.content == b""
    assert health == GatewayHealth(
        provisioned=True,
        healthy=True,
        gateway_id="260917-opensre-org_abc-gateway",
        desired_state="running",
        actual_state="running",
        size_profile="MEDIUM",
        updated_at="2026-09-18T10:00:00Z",
    )


@pytest.mark.parametrize(
    "app_url",
    ["http://app.opensre.com", "http://attacker.example", "ftp://app.test", "app.test", ""],
)
def test_the_token_is_never_sent_over_an_insecure_origin(app_url: str) -> None:
    # Arrange
    def never(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"no request may leave for {request.url}")

    # Act
    with pytest.raises(HostedGatewayError) as excinfo:
        _client(httpx.MockTransport(never), app_url=app_url)

    # Assert
    assert excinfo.value.code == ERR_INSECURE_APP_URL


@pytest.mark.parametrize("app_url", ["http://localhost:3000", "http://127.0.0.1:3000"])
def test_plain_http_is_allowed_only_to_this_machine(app_url: str) -> None:
    # Arrange
    def answer(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"provisioned": False, "healthy": False})

    # Act
    with _client(httpx.MockTransport(answer), app_url=app_url) as client:
        health = client.health()

    # Assert
    assert health == GatewayHealth(provisioned=False, healthy=False)


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(401, json={"error": "unauthorized"}), ERR_UNAUTHORIZED),
        (httpx.Response(404, text="<html>no such route</html>"), ERR_NOT_SUPPORTED),
        (httpx.Response(502, json={"error": "gateway_lookup_failed"}), "http_502"),
        (httpx.Response(200, text="<html>not json</html>"), ERR_INVALID_RESPONSE),
        (httpx.Response(200, json={"provisioned": "yes", "healthy": True}), ERR_INVALID_RESPONSE),
        (httpx.Response(200, json=["not", "an", "object"]), ERR_INVALID_RESPONSE),
    ],
)
def test_refusals_and_malformed_answers_become_stable_codes_without_the_token(
    response: httpx.Response, code: str
) -> None:
    # Arrange
    client = _client(httpx.MockTransport(lambda _request: response))

    # Act
    with pytest.raises(HostedGatewayError) as excinfo:
        client.health()

    # Assert
    assert excinfo.value.code == code
    assert _TOKEN not in str(excinfo.value) and _TOKEN not in repr(excinfo.value)


def test_a_network_failure_is_reported_as_unreachable_without_the_token() -> None:
    # Arrange
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _client(httpx.MockTransport(fail))

    # Act
    with pytest.raises(HostedGatewayError) as excinfo:
        client.health()

    # Assert
    assert excinfo.value.code == ERR_UNREACHABLE
    assert _TOKEN not in str(excinfo.value)


def test_redirects_are_not_followed_so_the_token_cannot_be_forwarded_to_another_host() -> None:
    # Arrange
    hosts: list[str] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://attacker.example/steal"})

    client = _client(httpx.MockTransport(redirect))

    # Act
    with pytest.raises(HostedGatewayError) as excinfo:
        client.health()

    # Assert
    assert hosts == ["app.test"]
    assert excinfo.value.code == "http_302"


def test_without_a_sign_in_no_client_is_built(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setattr("integrations.hosted_gateway.client.load_account_record", lambda: None)
    monkeypatch.setattr("integrations.hosted_gateway.client.resolve_account_token", lambda: "")

    # Act
    with pytest.raises(HostedGatewayError) as excinfo:
        HostedGatewayClient.from_account()

    # Assert
    assert excinfo.value.code == ERR_NOT_SIGNED_IN
