"""Each turn's request extends the previous turn's byte for byte, so the prompt cache covers it.

Anthropic caches the request prefix — tools, then system, then messages — up to
a breakpoint, and any byte that changes inside it re-prices everything after;
OpenAI caches the longest prefix it has seen. These tests run real turns through
the harness, the default tool catalog and the provider's agent client; only the
HTTP call is replaced, by a scripted SDK client that records each request body.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable, Iterable
from io import StringIO
from types import SimpleNamespace
from typing import Any

import pytest
from anthropic.types import Message
from openai.types.chat import ChatCompletion
from openai.types.responses import Response
from pydantic import BaseModel
from rich.console import Console

from config.constants import OPENSRE_MEMORY_DISABLED_ENV
from config.constants.llm import ANTHROPIC_THINKING_BINDING_KEY
from core.agent_harness import SessionCore
from core.agent_harness.prompts.action.assemble import SESSION_CONTEXT_HEADER
from core.agent_harness.session.lifecycle import SessionManager
from core.agent_harness.session.persistence.contracts import RestoreContextKey
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.tools.tool_provider import DefaultToolProvider
from core.agent_harness.turns.headless_build import InMemoryHeadlessBuild
from core.llm.transports.sdk.agent_clients import AnthropicAgentClient, OpenAIAgentClient
from tests.utils.anthropic_streams import FakeMessageStream
from tests.utils.openai_streams import (
    FakeChunkStream,
    FakeResponseStream,
    completion_chunks,
    response_text_events,
)

_REMEMBER = {
    "name": "payments-deploy-day",
    "type": "infrastructure",
    "description": "Payments deploys every Tuesday",
    "content": "The payments service deploys every Tuesday.",
    "source": "user",
    "evidence": "payments deploys every Tuesday",
}


def _sdk_model(value: Any) -> Any:
    """Encode an SDK object the way the SDK encodes a request body."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_unset=True, by_alias=True)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _without_markers(node: Any) -> Any:
    """Drop what the API ignores when it matches a prefix: cache markers, string vs text block."""
    if isinstance(node, dict):
        stripped = {key: _without_markers(value) for key, value in node.items()}
        stripped.pop("cache_control", None)
        if isinstance(stripped.get("content"), str) and "role" in stripped:
            stripped["content"] = [{"type": "text", "text": stripped["content"]}]
        return stripped
    if isinstance(node, list):
        return [_without_markers(item) for item in node]
    return node


class _ScriptedAnthropic:
    """Stands in for the Anthropic SDK client: records each request, returns the next reply.

    The agent client streams; ``stream`` records and replies the same way ``create`` does.
    """

    def __init__(self, replies: Iterable[list[dict[str, Any]]]) -> None:
        self.messages = self
        self.requests: list[dict[str, Any]] = []
        self._replies = iter(replies)
        self._ids = itertools.count(1)

    def create(self, **kwargs: Any) -> Message:
        wire = json.dumps(kwargs, ensure_ascii=False, default=_sdk_model)
        self.requests.append(_without_markers(json.loads(wire)))
        content = next(self._replies)
        return Message.model_validate(
            {
                "id": f"msg_{next(self._ids)}",
                "type": "message",
                "role": "assistant",
                "model": kwargs["model"],
                "content": content,
                "stop_reason": "tool_use"
                if any(block["type"] == "tool_use" for block in content)
                else "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    def stream(self, **kwargs: Any) -> FakeMessageStream:
        return FakeMessageStream(self.create(**kwargs))


class _ScriptedOpenAI:
    """Stands in for the OpenAI SDK client on both endpoints: records each request, returns the next reply.

    A Responses reply is a list of output items; a Chat Completions reply is the
    assistant message. Streamed requests get the same reply as stream events.
    """

    def __init__(self, replies: Iterable[Any]) -> None:
        self.requests: list[dict[str, Any]] = []
        self._replies = iter(replies)
        self._ids = itertools.count(1)
        self.responses = SimpleNamespace(create=self._respond, stream=self._stream_response)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._complete))

    def _record(self, kwargs: dict[str, Any]) -> None:
        self.requests.append(json.loads(json.dumps(kwargs, ensure_ascii=False, default=_sdk_model)))

    def _respond(self, **kwargs: Any) -> Response:
        self._record(kwargs)
        return Response.model_validate(
            {
                "id": f"resp_{next(self._ids)}",
                "object": "response",
                "created_at": 0,
                "model": kwargs["model"],
                "status": "completed",
                "parallel_tool_calls": True,
                "tool_choice": "auto",
                "tools": [],
                "output": next(self._replies),
                "usage": {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                    "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            }
        )

    def _stream_response(self, **kwargs: Any) -> FakeResponseStream:
        response = self._respond(**kwargs)
        return FakeResponseStream(response_text_events(response), response)

    def _complete(self, **kwargs: Any) -> ChatCompletion | FakeChunkStream:
        self._record(kwargs)
        message = next(self._replies)
        completion = ChatCompletion.model_validate(
            {
                "id": f"chatcmpl-{next(self._ids)}",
                "object": "chat.completion",
                "created": 0,
                "model": kwargs["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
                        "message": message,
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )
        return (
            FakeChunkStream(completion_chunks(completion)) if kwargs.get("stream") else completion
        )


def _tool_use(call_id: str, name: str, tool_input: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"type": "text", "text": "Checking."},
        {"type": "tool_use", "id": call_id, "name": name, "input": tool_input},
    ]


def _text(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": text}]


def _thinking(signature: str) -> dict[str, Any]:
    """A thinking block as Opus 5.5 / Sonnet 5.5 / Fable return it by default: text omitted."""
    return {"type": "thinking", "thinking": "", "signature": signature}


def _compact_json(value: Any) -> str:
    """Arguments the way a model writes them, without the spaces ``json.dumps`` adds."""
    return json.dumps(value, separators=(",", ":"))


def _responses_call(number: int, name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"type": "reasoning", "id": f"rs_{number}", "summary": []},
        {
            "type": "function_call",
            "id": f"fc_{number}",
            "call_id": f"call_{number}",
            "name": name,
            "arguments": _compact_json(arguments),
            "status": "completed",
        },
    ]


def _responses_text(number: int, text: str) -> list[dict[str, Any]]:
    return [
        {"type": "reasoning", "id": f"rs_{number}", "summary": []},
        {
            "type": "message",
            "id": f"msg_{number}",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        },
    ]


def _chat_call(number: int, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "refusal": None,
        "annotations": [],
        "tool_calls": [
            {
                "id": f"call_{number}",
                "type": "function",
                "function": {"name": name, "arguments": _compact_json(arguments)},
            }
        ],
    }


def _chat_text(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text, "refusal": None, "annotations": []}


def _call_arguments(items: list[dict[str, Any]]) -> list[str]:
    """Each tool call's arguments string, from Responses input items or Chat Completions messages."""
    arguments = [str(item["arguments"]) for item in items if item.get("type") == "function_call"]
    for message in items:
        arguments.extend(
            str(call["function"]["arguments"]) for call in message.get("tool_calls") or ()
        )
    return arguments


def _dump(part: Any) -> str:
    return json.dumps(part, ensure_ascii=False)


def _assert_extends(
    previous: dict[str, Any], current: dict[str, Any], *, messages: str = "messages"
) -> None:
    """``current`` repeats ``previous`` byte for byte before its first new message."""
    assert _dump(current["tools"]) == _dump(previous["tools"])
    assert _dump(current.get("system")) == _dump(previous.get("system"))
    shared = len(previous[messages])
    for index, message in enumerate(previous[messages]):
        assert _dump(current[messages][index]) == _dump(message), f"{messages}[{index}] changed"
    assert len(current[messages]) > shared


def _user_text(message: dict[str, Any]) -> str:
    return "".join(block.get("text", "") for block in message["content"])


def _rebuilt_from_log(session: SessionCore) -> SessionCore:
    """The session the gateway resolves for the next message: a new object restored from its log."""
    data = {
        RestoreContextKey.CLI_AGENT_MESSAGES: [list(pair) for pair in session.cli_agent_messages],
        RestoreContextKey.TURN_EVIDENCE: [
            json.loads(json.dumps(record.to_json())) for record in session.turn_evidence
        ],
    }
    rebuilt = SessionCore(session_id=session.session_id, store=session.store)
    return SessionManager(store=session.store).restore_context(rebuilt, data)


def _run_turns(llm_factory: Callable[[], Any], *, rebuild: bool) -> None:
    """Three turns through the harness: turn one reveals a tool with tool search and writes a memory.

    With ``rebuild`` the session is restored from its log before each next
    turn, as the gateway does for every message.
    """
    session = SessionCore(store=InMemorySessionStore())
    agent = InMemoryHeadlessBuild(session=session).agent(
        tools=DefaultToolProvider(session, Console(file=StringIO(), force_terminal=False)),
        llm_factory=llm_factory,
    )
    for message in (
        "Remember that payments deploys every Tuesday.",
        "When does payments deploy?",
        "Thanks!",
    ):
        agent.dispatch(message)
        if rebuild:
            session = _rebuilt_from_log(session)
            agent.bind_session(session)


def _three_turns(model: str, *, rebuild: bool) -> list[dict[str, Any]]:
    """Anthropic requests of :func:`_run_turns`; turn one reveals and calls ``memory_remember``."""
    sdk = _ScriptedAnthropic(
        [
            _tool_use("toolu_01", "tool_search", {"names": ["memory_remember"]}),
            _tool_use("toolu_02", "memory_remember", _REMEMBER),
            _text("Saved: payments deploys on Tuesdays."),
            _text("Payments deploys on Tuesdays."),
            _text("Anytime."),
        ]
    )
    _run_turns(lambda: AnthropicAgentClient(model=model, client=sdk), rebuild=rebuild)
    return sdk.requests


@pytest.fixture(autouse=True)
def _memory_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPENSRE_MEMORY_DISABLED_ENV, raising=False)


@pytest.mark.parametrize("rebuild", [False, True], ids=["live_session", "rebuilt_from_log"])
def test_the_next_turn_repeats_the_previous_request_byte_for_byte(rebuild: bool) -> None:
    """Pins the four ways the prefix used to change between turns.

    Turn one reveals a hidden tool with tool search and writes a memory, which
    changes both the tool list and the session context. The next turn must still
    resend everything the last request of turn one sent — the user message with
    its context, the tool calls and results, the tools — byte for byte, and
    carry the changed context in its own new message instead. The gateway
    rebuilds the session from its log for every message, so the same must hold
    for a session restored between turns. The model here cannot load tools by
    reference, so the reveal grows the tool list (see the next test).
    """
    first, _found, last_of_turn_one, turn_two, turn_three = _three_turns(
        "claude-test", rebuild=rebuild
    )
    _assert_extends(last_of_turn_one, turn_two)
    _assert_extends(turn_two, turn_three)
    # Session context rides in the conversation, never in the cached system prompt.
    assert "LONG-TERM MEMORY" not in _dump(first["system"])
    assert SESSION_CONTEXT_HEADER in _user_text(first["messages"][0])
    # The memory written in turn one reaches turn two as a new section of its own
    # message; turn three, with nothing changed, sends none.
    assert "payments-deploy-day" not in _user_text(first["messages"][0])
    assert "payments-deploy-day" in _user_text(turn_two["messages"][-1])
    assert SESSION_CONTEXT_HEADER not in _user_text(turn_three["messages"][-1])
    # The tool tool search revealed stays visible after its turn.
    assert "memory_remember" not in {tool["name"] for tool in first["tools"]}
    assert "memory_remember" in {tool["name"] for tool in turn_two["tools"]}


@pytest.mark.parametrize("rebuild", [False, True], ids=["live_session", "rebuilt_from_log"])
def test_a_tool_search_reveal_keeps_the_request_prefix_byte_for_byte(rebuild: bool) -> None:
    """A model that loads tools by reference gets a reveal as new conversation, not new tools.

    Every catalog tool the conversation was not shown is declared deferred from
    the first request on, and the search result references the tool it
    revealed, so the request right after the reveal repeats the one before it
    byte for byte — and so does every later request, live or restored from the
    log. Anthropic renders tools first: a tool added mid-turn re-priced the
    whole prompt.
    """
    requests = _three_turns("claude-opus-4-7", rebuild=rebuild)

    for previous, current in itertools.pairwise(requests):
        _assert_extends(previous, current)
    first, found = requests[0], requests[1]
    deferred = {tool["name"] for tool in first["tools"] if tool.get("defer_loading")}
    assert "memory_remember" in deferred
    assert "tool_search" not in deferred
    (search_result,) = found["messages"][-1]["content"]
    assert {"type": "tool_reference", "tool_name": "memory_remember"} in search_result["content"]


@pytest.mark.parametrize("rebuild", [False, True], ids=["live_session", "rebuilt_from_log"])
def test_thinking_blocks_replay_with_their_turn_while_their_prefix_holds(rebuild: bool) -> None:
    """Thinking models: an earlier turn's tool calls go back with the thinking they were sent with.

    Opus 5.5, Sonnet 5.5 and Fable think by default, and the live loop echoes
    each thinking block, so a replay without them broke the next turn's prefix
    at the first earlier tool call. A block is also bound to everything before
    it, tools included: the one written before tool search grew the tool list
    would be rejected by the API's history-editing check once the list grew, so
    it is dropped from that request on, the same way in every later turn.
    """
    sdk = _ScriptedAnthropic(
        [
            [
                _thinking("sig-search"),
                *_tool_use("toolu_01", "tool_search", {"names": ["memory_remember"]}),
            ],
            [_thinking("sig-remember"), *_tool_use("toolu_02", "memory_remember", _REMEMBER)],
            [_thinking("sig-saved"), *_text("Saved: payments deploys on Tuesdays.")],
            [_thinking("sig-answer"), *_text("Payments deploys on Tuesdays.")],
            _text("Anytime."),
        ]
    )

    _run_turns(lambda: AnthropicAgentClient(model="claude-test", client=sdk), rebuild=rebuild)

    _first, found, last_of_turn_one, turn_two, turn_three = sdk.requests
    _assert_extends(last_of_turn_one, turn_two)
    _assert_extends(turn_two, turn_three)
    for request in (last_of_turn_one, turn_two, turn_three):
        assert "sig-remember" in _dump(request["messages"])
    for request in (found, last_of_turn_one, turn_two, turn_three):
        assert "sig-search" not in _dump(request["messages"])
    # The binding the client checks rides with the transcript, never on the wire.
    assert not any(ANTHROPIC_THINKING_BINDING_KEY in _dump(request) for request in sdk.requests)


@pytest.mark.parametrize("rebuild", [False, True], ids=["live_session", "rebuilt_from_log"])
def test_thinking_before_a_reveal_holds_when_tools_load_by_reference(rebuild: bool) -> None:
    """A reveal that loads its tool by reference keeps the thinking written before it.

    The binding covers the request as sent. On a model that loads tools by
    reference a reveal leaves that request's tools as they were, so the block
    written before the search still holds and every later request carries it.
    """
    sdk = _ScriptedAnthropic(
        [
            [
                _thinking("sig-search"),
                *_tool_use("toolu_01", "tool_search", {"names": ["memory_remember"]}),
            ],
            [_thinking("sig-remember"), *_tool_use("toolu_02", "memory_remember", _REMEMBER)],
            [_thinking("sig-saved"), *_text("Saved: payments deploys on Tuesdays.")],
            [_thinking("sig-answer"), *_text("Payments deploys on Tuesdays.")],
            _text("Anytime."),
        ]
    )

    _run_turns(lambda: AnthropicAgentClient(model="claude-opus-4-7", client=sdk), rebuild=rebuild)

    for previous, current in itertools.pairwise(sdk.requests):
        _assert_extends(previous, current)
    for request in sdk.requests[1:]:
        assert "sig-search" in _dump(request["messages"])


def _openai_client(model: str, sdk: _ScriptedOpenAI) -> Callable[[], OpenAIAgentClient]:
    def build() -> OpenAIAgentClient:
        client = OpenAIAgentClient(model=model, credential_resolver=lambda _env: "test-key")
        client._client = sdk
        return client

    return build


@pytest.mark.parametrize("rebuild", [False, True], ids=["live_session", "rebuilt_from_log"])
@pytest.mark.parametrize("api", ["responses", "chat_completions"])
def test_openai_turns_replay_each_tool_call_as_the_model_sent_it(
    api: str, rebuild: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The next turn resends an earlier OpenAI tool call exactly as the live request did.

    A Responses reply carries reasoning items its function calls are tied to by
    id; a Chat Completions reply carries the arguments as the model wrote them.
    Rebuilding either from the recorded text and calls changed the request at
    the first replayed tool call, and OpenAI's prefix cache missed from there.
    """
    monkeypatch.setattr("core.llm.hosted_credits.admit_hosted_credits", lambda: None)
    search = {"names": ["memory_remember"]}
    if api == "responses":
        model, key = "gpt-5.6", "input"
        replies: list[Any] = [
            _responses_call(1, "tool_search", search),
            _responses_call(2, "memory_remember", _REMEMBER),
            _responses_text(3, "Saved: payments deploys on Tuesdays."),
            _responses_text(4, "Payments deploys on Tuesdays."),
            _responses_text(5, "Anytime."),
        ]
    else:
        model, key = "gpt-5.4-mini", "messages"
        replies = [
            _chat_call(1, "tool_search", search),
            _chat_call(2, "memory_remember", _REMEMBER),
            _chat_text("Saved: payments deploys on Tuesdays."),
            _chat_text("Payments deploys on Tuesdays."),
            _chat_text("Anytime."),
        ]
    sdk = _ScriptedOpenAI(replies)

    _run_turns(_openai_client(model, sdk), rebuild=rebuild)

    _first, _found, last_of_turn_one, turn_two, turn_three = sdk.requests
    _assert_extends(last_of_turn_one, turn_two, messages=key)
    _assert_extends(turn_two, turn_three, messages=key)
    assert _compact_json(_REMEMBER) in _call_arguments(turn_two[key])
    if api == "responses":
        assert {"type": "reasoning", "id": "rs_2", "summary": []} in turn_two[key]
