"""Tests for sending a prompt to the hosted gateway: what is sent, and what the user is told."""

from __future__ import annotations

import json
from types import TracebackType

import httpx
import pytest

from core.agent_harness import SessionCore
from core.agent_harness.spi.handoff import AskUserQuestion, format_ask_user_answers
from core.agent_harness.tools import ActionToolScope
from core.agent_harness.tools.tool_context import ACTION_TOOL_CONTEXT_RESOURCE_KEY
from core.tool import AgentToolContext
from integrations.hosted_gateway import (
    ERR_ALREADY_ANSWERED,
    ERR_NOT_RUNNING,
    ERR_UNKNOWN_PROMPT,
    HostedGatewayClient,
    HostedGatewayError,
    PromptChoice,
    PromptQuestion,
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
        self.answered: list[tuple[str, str]] = []

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

    def answer_prompt(self, prompt_id: str, answer: str) -> PromptRecord:
        self.answered.append((prompt_id, answer))
        return self._states.pop(0)


def _tool_context(session: SessionCore, turn_user_message: str) -> AgentToolContext:
    scope = ActionToolScope(session=session, console=None, turn_user_message=turn_user_message)
    return AgentToolContext(
        resolved_integrations={}, resources={ACTION_TOOL_CONTEXT_RESOURCE_KEY: scope}
    )


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
    out = ask_hosted_gateway(prompt="which tasks run?", facts={"repository": "o/r"})

    # Assert
    assert app.sent == [("which tasks run?", {"repository": "o/r"})]
    assert app.polled == [_ID, _ID]
    assert out["success"] is True and out["state"] == "done"
    assert out["response_text"] == "4 tasks; the CI repair loop is among them."


def test_a_question_from_the_gateway_opens_this_shells_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: a shell session behind the tool, and a gateway that stopped on a question
    choice = PromptChoice(
        title="Approve schedule_ci_repair_loop?",
        questions=(PromptQuestion("Approve schedule_ci_repair_loop?", ("Approve", "Deny")),),
        custom_answer=False,
        note="Starts a background worker.",
    )
    app = _App([PromptRecord(_ID, "needs_input", question="Approve?", choice=choice)])
    _signed_in_with(monkeypatch, app)
    session = SessionCore()

    # Act
    out = ask_hosted_gateway(prompt="schedule the loop", context=_tool_context(session, ""))

    # Assert: the question is parked as the shell's own menu, with the approval details
    parked = session.pending_user_choice
    assert out["state"] == "needs_input" and "The menu opens now" in out["response_text"]
    assert parked is not None and parked.options == ("Approve", "Deny")
    assert parked.note == "Starts a background worker." and parked.custom_answer is False
    assert parked.interaction_id == f"hosted_prompt:{_ID}"
    assert out["choice"]["note"] == "Starts a background worker."


def test_the_answer_comes_from_the_users_selection_never_from_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the same asked prompt, read twice: once in a turn the user answered, once not
    question = PromptQuestion("Which branch?", ("main", "release"))
    asked = PromptRecord(
        _ID,
        "needs_input",
        question="Which branch?",
        choice=PromptChoice("Which branch?", (question,)),
    )
    follow_up = PromptRecord("p_" + "b" * 32, "done", answer="Loop scheduled on release.")
    app = _App([asked, asked, follow_up])
    _signed_in_with(monkeypatch, app)
    answered_turn = format_ask_user_answers(
        (AskUserQuestion(label="", title="Which branch?", options=("main", "release")),),
        ("release",),
    )

    # Act
    unanswered = ask_hosted_gateway(prompt_id=_ID, context=_tool_context(SessionCore(), ""))
    sent_without_a_pick = list(app.answered)
    answered = ask_hosted_gateway(
        prompt_id=_ID, context=_tool_context(SessionCore(), answered_turn)
    )

    # Assert: nothing is sent until the shell's own message carries the user's pick
    assert unanswered["state"] == "needs_input" and sent_without_a_pick == []
    assert answered["state"] == "done" and app.answered == [(_ID, "release")]


def test_several_questions_go_back_as_one_json_object_keyed_by_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    questions = (
        PromptQuestion("Scope?", ("a", "b")),
        PromptQuestion("Window?", ("1h", "24h")),
    )
    asked = PromptRecord(
        _ID, "needs_input", question="Setup", choice=PromptChoice("Setup", questions)
    )
    app = _App([asked, PromptRecord("p_" + "c" * 32, "done", answer="ok")])
    _signed_in_with(monkeypatch, app)
    turn = format_ask_user_answers(
        (
            AskUserQuestion(label="", title="Scope?", options=("a", "b")),
            AskUserQuestion(label="", title="Window?", options=("1h", "24h")),
        ),
        ("b", "24h"),
    )

    # Act
    ask_hosted_gateway(prompt_id=_ID, context=_tool_context(SessionCore(), turn))

    # Assert
    assert app.answered == [(_ID, json.dumps({"Scope?": "b", "Window?": "24h"}))]


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
    assert set(tool.input_schema["properties"]) == {"prompt", "facts", "prompt_id"}
    assert tool.accepts_runtime_context is True
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


def test_answer_prompt_posts_to_the_answer_route_and_keeps_the_apps_refusal_code() -> None:
    # Arrange
    seen: list[httpx.Request] = []
    follow_up = "p_" + "b" * 32

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(202, json={"prompt_id": follow_up, "state": "queued"})
        return httpx.Response(409, json={"error": "already_answered"})

    # Act
    with _client(httpx.MockTransport(answer)) as client:
        record = client.answer_prompt(_ID, "Approve")
        with pytest.raises(HostedGatewayError) as refused:
            client.answer_prompt(_ID, "Approve")

    # Assert
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == f"/api/agent-backend/gateway/prompts/{_ID}/answer"
    assert json.loads(request.content) == {"answer": "Approve"}
    assert record == PromptRecord(prompt_id=follow_up, state="queued")
    assert refused.value.args[0] == ERR_ALREADY_ANSWERED


def test_a_needs_input_record_carries_the_structured_choice() -> None:
    # Arrange
    def answer(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "prompt_id": _ID,
                "state": "needs_input",
                "question": "Approve schedule_ci_repair_loop?",
                "choice": {
                    "title": "Approve schedule_ci_repair_loop?",
                    "questions": [
                        {
                            "title": "Approve schedule_ci_repair_loop?",
                            "options": ["Approve", "Deny"],
                            "multi_select": False,
                        }
                    ],
                    "custom_answer": False,
                },
            },
        )

    # Act
    with _client(httpx.MockTransport(answer)) as client:
        record = client.prompt_result(_ID)

    # Assert
    assert record.choice == PromptChoice(
        title="Approve schedule_ci_repair_loop?",
        questions=(
            PromptQuestion(title="Approve schedule_ci_repair_loop?", options=("Approve", "Deny")),
        ),
        custom_answer=False,
    )
