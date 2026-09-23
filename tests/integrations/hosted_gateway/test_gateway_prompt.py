"""Tests for sending a prompt to the hosted gateway: what is sent, and what the user is told."""

from __future__ import annotations

import json
from types import TracebackType

import httpx
import pytest

from integrations.hosted_gateway import (
    ERR_NOT_RUNNING,
    ERR_UNKNOWN_PROMPT,
    HostedGatewayClient,
    HostedGatewayError,
    PromptRecord,
)
from integrations.hosted_gateway.tools import gateway_prompt
from integrations.hosted_gateway.tools.gateway_prompt import ask_hosted_gateway
from tools.registry import clear_tool_registry_cache, get_registered_tool_map

_TOKEN = "osre_pat_test_token_value"
_ID = "p_" + "a" * 32


def _client(transport: httpx.MockTransport) -> HostedGatewayClient:
    return HostedGatewayClient(app_url="https://app.test", token=_TOKEN, transport=transport)


def test_send_prompt_posts_the_prompt_and_context_with_the_token_only() -> None:
    # Arrange
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json={"prompt_id": _ID, "state": "queued"})

    # Act
    with _client(httpx.MockTransport(answer)) as client:
        record = client.send_prompt("which tasks run?", context={"repository": "o/r"})

    # Assert: no organization or gateway named anywhere; the token is the only identity.
    request = seen[0]
    assert request.method == "POST" and request.url.path == "/api/agent-backend/gateway/prompts"
    assert json.loads(request.content) == {
        "prompt": "which tasks run?",
        "context": {"repository": "o/r"},
    }
    assert request.headers["authorization"] == f"Bearer {_TOKEN}"
    assert "org" not in str(request.url) and "organization" not in request.content.decode()
    assert record == PromptRecord(prompt_id=_ID, state="queued")


@pytest.mark.parametrize(
    ("method", "status", "code"),
    [
        ("send", 409, ERR_NOT_RUNNING),
        ("send", 413, "prompt_too_large"),
        ("read", 404, ERR_UNKNOWN_PROMPT),
        ("read", 409, ERR_NOT_RUNNING),
    ],
)
def test_refusals_become_stable_codes(method: str, status: int, code: str) -> None:
    # Arrange
    client = _client(httpx.MockTransport(lambda _r: httpx.Response(status, json={"error": code})))

    # Act
    with pytest.raises(HostedGatewayError) as excinfo:
        if method == "send":
            client.send_prompt("x", context={})
        else:
            client.prompt_result(_ID)

    # Assert
    assert excinfo.value.code == code
    assert _TOKEN not in str(excinfo.value)


def test_an_id_that_is_not_a_prompt_id_never_reaches_the_network() -> None:
    # Arrange
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    client = _client(httpx.MockTransport(record))

    # Act
    with pytest.raises(HostedGatewayError) as excinfo:
        client.prompt_result("../health")

    # Assert
    assert excinfo.value.code == ERR_UNKNOWN_PROMPT
    assert requests == []


class _App:
    """A fake signed-in client whose gateway settles after a given number of polls."""

    app_url = "https://app.test"

    def __init__(self, states: list[PromptRecord]) -> None:
        self._states = list(states)
        self.sent: list[tuple[str, dict[str, str]]] = []
        self.polled: list[str] = []

    def __enter__(self) -> _App:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    def send_prompt(self, prompt: str, *, context: dict[str, str]) -> PromptRecord:
        self.sent.append((prompt, context))
        return self._states.pop(0)

    def prompt_result(self, prompt_id: str) -> PromptRecord:
        self.polled.append(prompt_id)
        return self._states.pop(0)


def _signed_in_with(monkeypatch: pytest.MonkeyPatch, app: _App) -> None:
    monkeypatch.setattr(gateway_prompt.HostedGatewayClient, "from_account", lambda: app)
    monkeypatch.setattr(gateway_prompt, "HOSTED_GATEWAY_PROMPT_POLL_SECONDS", 0.0)


def test_the_tool_waits_for_the_answer_and_returns_it(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    app = _App(
        [
            PromptRecord(_ID, "queued"),
            PromptRecord(_ID, "running"),
            PromptRecord(_ID, "done", answer="4 tasks; the CI repair loop is among them."),
        ]
    )
    _signed_in_with(monkeypatch, app)

    # Act
    out = ask_hosted_gateway(prompt="which tasks run?", context={"repository": "o/r"})

    # Assert
    assert app.sent == [("which tasks run?", {"repository": "o/r"})]
    assert app.polled == [_ID, _ID]
    assert out["success"] is True and out["state"] == "done"
    assert out["response_text"] == "4 tasks; the CI repair loop is among them."


def test_a_question_from_the_gateway_is_relayed_as_needs_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    app = _App([PromptRecord(_ID, "needs_input", question="Which branch?\nOptions: main, release")])
    _signed_in_with(monkeypatch, app)

    # Act
    out = ask_hosted_gateway(prompt="fix ci")

    # Assert
    assert out["success"] is True and out["state"] == "needs_input"
    assert "It asked: Which branch?" in out["response_text"]
    assert "Send the prompt again" in out["response_text"]


def test_reading_an_earlier_prompt_sends_nothing_new(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    app = _App([PromptRecord(_ID, "failed", error="turn_failed")])
    _signed_in_with(monkeypatch, app)

    # Act
    out = ask_hosted_gateway(prompt_id=_ID)

    # Assert
    assert app.sent == [] and app.polled == [_ID]
    assert out["state"] == "failed" and "(turn_failed)" in out["response_text"]


def test_the_wait_budget_hands_back_the_prompt_id(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    app = _App([PromptRecord(_ID, "queued"), PromptRecord(_ID, "running")])
    _signed_in_with(monkeypatch, app)
    monkeypatch.setattr(gateway_prompt, "HOSTED_GATEWAY_PROMPT_WAIT_SECONDS", 0.0)

    # Act
    out = ask_hosted_gateway(prompt="slow one")

    # Assert
    assert out["success"] is False and out["state"] == "queued"
    assert _ID in out["response_text"] and "still working" in out["response_text"]


def test_the_tool_is_external_takes_no_identifier_and_refuses_an_empty_request() -> None:
    # Arrange
    clear_tool_registry_cache()
    tool = get_registered_tool_map()["ask_hosted_gateway"]

    # Act
    out = ask_hosted_gateway()

    # Assert
    assert tool.side_effect_level == "external"
    assert set(tool.input_schema["properties"]) == {"prompt", "context", "prompt_id"}
    assert out["success"] is False and "Give the hosted gateway a prompt" in out["response_text"]


def test_a_failed_integration_on_the_gateway_points_the_user_to_the_integrations_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    app = _App([PromptRecord(_ID, "done", answer="16 open PRs", failed_integrations=("github",))])
    _signed_in_with(monkeypatch, app)

    # Act
    out = ask_hosted_gateway(prompt="count open PRs")

    # Assert
    assert out["failed_integrations"] == ["github"]
    assert out["response_text"].startswith("16 open PRs")
    assert "returned errors on the hosted gateway: github" in out["response_text"]
    assert "https://app.test/integrations" in out["response_text"]


def test_the_client_reads_failed_integrations_from_the_record() -> None:
    # Arrange
    payload = {
        "prompt_id": _ID,
        "state": "done",
        "answer": "x",
        "failed_integrations": ["github", 3, ""],
    }
    client = _client(httpx.MockTransport(lambda _r: httpx.Response(200, json=payload)))

    # Act
    record = client.prompt_result(_ID)

    # Assert: only well-formed names survive
    assert record.failed_integrations == ("github",)
