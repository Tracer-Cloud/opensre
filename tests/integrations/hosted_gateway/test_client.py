"""Tests for the hosted-gateway client: who the token is sent to, and what comes back."""

from __future__ import annotations

from http import HTTPStatus

import httpx
import pytest

from integrations.hosted_gateway import (
    ERR_GATEWAY_UNAVAILABLE,
    ERR_INSECURE_APP_URL,
    ERR_INVALID_RESPONSE,
    ERR_NOT_SIGNED_IN,
    ERR_NOT_SUPPORTED,
    ERR_TOO_MANY_PROMPTS,
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
        # The app answered for a gateway that did not: retryable, not an unknown failure.
        (
            httpx.Response(HTTPStatus.BAD_GATEWAY, json={"error": "gateway_lookup_failed"}),
            ERR_GATEWAY_UNAVAILABLE,
        ),
        # Health has no provisioning refusal: these are unexpected, reportable failures.
        (httpx.Response(403, text="<html>blocked</html>"), "http_403"),
        (httpx.Response(409, json={"error": "conflict"}), "http_409"),
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


def test_an_unavailable_gateway_keeps_the_cause_the_app_named() -> None:
    """A 502 used to drop the app's code, so every outage sounded like a restart."""
    # Arrange
    response = httpx.Response(HTTPStatus.BAD_GATEWAY, json={"error": "GATEWAY_UNREACHABLE"})
    client = _client(httpx.MockTransport(lambda _request: response))

    # Act
    with pytest.raises(HostedGatewayError) as excinfo:
        client.health()

    # Assert
    assert excinfo.value.code == ERR_GATEWAY_UNAVAILABLE
    assert excinfo.value.cause_code == "GATEWAY_UNREACHABLE"
    assert _TOKEN not in str(excinfo.value)


@pytest.mark.parametrize(
    "error",
    [
        "connection refused while using " + _TOKEN,
        _TOKEN,
        "A" * 65,
    ],
)
def test_a_cause_that_is_not_a_stable_code_is_dropped(error: str) -> None:
    # Arrange
    response = httpx.Response(HTTPStatus.BAD_GATEWAY, json={"error": error})
    client = _client(httpx.MockTransport(lambda _request: response))

    # Act
    with pytest.raises(HostedGatewayError) as excinfo:
        client.health()

    # Assert
    assert excinfo.value.code == ERR_GATEWAY_UNAVAILABLE
    assert excinfo.value.cause_code == ""
    assert _TOKEN not in str(excinfo.value) and _TOKEN not in repr(excinfo.value)


def test_a_capacity_refusal_keeps_the_status_and_names_the_cause() -> None:
    """409 stays not_running for callers that branch on it; the cause says why."""
    # Arrange
    response = httpx.Response(HTTPStatus.CONFLICT, json={"error": "GATEWAY_CAPACITY_EXCEEDED"})
    client = _client(httpx.MockTransport(lambda _request: response))

    # Act
    with pytest.raises(HostedGatewayError) as excinfo:
        client.send_prompt("delegate the demo", context={})

    # Assert
    assert excinfo.value.code == "not_running"
    assert excinfo.value.cause_code == "GATEWAY_CAPACITY_EXCEEDED"


@pytest.mark.parametrize(
    ("status", "retry_after"),
    [
        (HTTPStatus.SERVICE_UNAVAILABLE, "10"),
        (HTTPStatus.TOO_MANY_REQUESTS, "10"),
        # An app deployed before it kept the gateway's 503 relays the refusal as a 502.
        (HTTPStatus.BAD_GATEWAY, None),
    ],
)
def test_a_full_prompt_queue_is_a_capacity_refusal_that_is_not_resent(
    status: HTTPStatus, retry_after: str | None
) -> None:
    """Load test: each refused prompt reached the gateway twice, resent as a transient 502."""
    # Arrange
    attempts: list[httpx.Request] = []
    headers = {"Retry-After": retry_after} if retry_after else {}

    def queue_full(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(status, json={"error": "too_many_prompts"}, headers=headers)

    client = _client(httpx.MockTransport(queue_full))

    # Act
    with pytest.raises(HostedGatewayError) as excinfo:
        client.send_prompt("delegate the demo", context={}, request_id="req_capacity_1")

    # Assert
    assert excinfo.value.code == ERR_TOO_MANY_PROMPTS
    assert excinfo.value.status == status
    assert excinfo.value.retry_after == (int(retry_after) if retry_after else None)
    assert len(attempts) == 1


def test_a_cause_that_repeats_the_status_code_is_not_recorded_again() -> None:
    # Arrange
    response = httpx.Response(HTTPStatus.CONFLICT, json={"error": "not_running"})
    client = _client(httpx.MockTransport(lambda _request: response))

    # Act
    with pytest.raises(HostedGatewayError) as excinfo:
        client.send_prompt("delegate the demo", context={})

    # Assert
    assert excinfo.value.code == "not_running"
    assert excinfo.value.cause_code == ""


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


def test_a_connection_that_could_not_be_made_is_tried_once_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A TLS handshake timeout left the request on this machine; one fresh try is safe."""
    # Arrange
    from integrations.hosted_gateway import client as client_module

    submitted: list[str] = []
    monkeypatch.setattr(client_module, "capture_hosted_gateway_task_submitted", submitted.append)
    prompt_id = "p_" + "b" * 32
    attempts: list[httpx.Request] = []

    def handshake_times_out_once(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.ConnectTimeout("The handshake operation timed out", request=request)
        return httpx.Response(HTTPStatus.ACCEPTED, json={"prompt_id": prompt_id, "state": "queued"})

    # Act
    with _client(httpx.MockTransport(handshake_times_out_once)) as client:
        record = client.send_prompt("probe github access", context={})

    # Assert
    assert record.prompt_id == prompt_id
    assert len(attempts) == 2
    assert submitted == [prompt_id]


def test_a_read_timeout_is_not_retried_so_a_prompt_is_never_queued_twice() -> None:
    """The app may have accepted the prompt before the answer timed out."""
    # Arrange
    attempts: list[httpx.Request] = []

    def answer_times_out(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        raise httpx.ReadTimeout("timed out", request=request)

    client = _client(httpx.MockTransport(answer_times_out))

    # Act
    with pytest.raises(HostedGatewayError) as excinfo:
        client.send_prompt("probe github access", context={})

    # Assert
    assert excinfo.value.code == ERR_UNREACHABLE
    assert len(attempts) == 1


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


def test_submission_telemetry_requires_new_accepted_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Polling, answering, and refused submissions must not count as new tasks."""
    from integrations.hosted_gateway import client as client_module

    captured: list[str] = []
    monkeypatch.setattr(client_module, "capture_hosted_gateway_task_submitted", captured.append)
    prompt_id = "p_" + "a" * 32

    def answer(request: httpx.Request) -> httpx.Response:
        if b"refuse-this-task" in request.content:
            return httpx.Response(409, json={"error": "not_running"})
        return httpx.Response(
            200 if request.method == "GET" else 202,
            json={"prompt_id": prompt_id, "state": "queued"},
        )

    with _client(httpx.MockTransport(answer)) as client:
        client.send_prompt("new task", context={})
        client.prompt_result(prompt_id)
        client.answer_prompt(prompt_id, "continue")
        with pytest.raises(HostedGatewayError):
            client.send_prompt("refuse-this-task", context={})
    assert captured == [prompt_id]
