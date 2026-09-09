from __future__ import annotations

import builtins
import logging
from collections.abc import Iterator
from dataclasses import replace
from typing import Any, cast

import pytest

from core.agent import Agent, AgentRunResult
from core.agent_harness.runtime import TurnBinding
from core.agent_harness.turns.headless_build import InMemoryHeadlessBuild
from core.events import (
    MessageUpdateEvent,
    RuntimeEvent,
    ToolExecutionUpdateEvent,
)
from core.llm.types import AgentLLMResponse, ToolCall
from core.messages import (
    AppRuntimeMessage,
    MessageMapper,
    ToolResultRuntimeMessage,
    UserRuntimeMessage,
)
from core.provider import ProviderHooks
from core.tool.contracts import AgentTool, AgentToolContext, RegisteredTool


class FakeLLM:
    """Duck-typed agent LLM client driving a scripted response sequence.

    Deliberately NOT a subclass of any real provider client so that the
    isinstance branches in ``build_assistant_message`` / ``build_tool_result_messages``
    fall through to the generic path.
    """

    def __init__(self, responses: Iterator[AgentLLMResponse]) -> None:
        self._responses = responses
        self.invocations = 0
        self.schema_tool_names: list[list[str]] = []
        self.seen_messages: list[list[dict[str, Any]]] = []
        self.seen_tools: list[list[dict[str, Any]] | None] = []
        self.model_id: str | None = None

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        self.schema_tool_names.append([t.name for t in tools])
        return [{"name": t.name} for t in tools]

    def invoke(
        self,
        messages: list[dict[str, Any]],  # noqa: ARG002
        *,
        system: str | None = None,  # noqa: ARG002
        tools: list[dict[str, Any]] | None = None,  # noqa: ARG002
    ) -> AgentLLMResponse:
        self.invocations += 1
        self.seen_messages.append(messages)
        self.seen_tools.append(tools)
        return next(self._responses)

    def build_assistant_message(
        self,
        content: str,
        tool_calls: list[ToolCall],
    ) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [{"id": tc.id, "name": tc.name} for tc in tool_calls],
        }

    def build_tool_result_message(
        self,
        tool_calls: list[ToolCall],
        results: list[Any],
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "results": [{"id": tc.id, "output": output} for tc, output in zip(tool_calls, results)],
        }


class FakeTool:
    """Minimal stand-in exposing only what ``execute_tools`` touches."""

    def __init__(self, name: str, output: dict[str, Any] | None = None) -> None:
        self.name = name
        self._output = output if output is not None else {"ok": True}

    def validate_public_input(self, value: dict[str, Any]) -> str | None:  # noqa: ARG002
        return None

    def extract_params(self, resolved: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        return {}

    def run(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ARG002
        return self._output


def _tools(*tools: FakeTool) -> list[RegisteredTool]:
    return cast("list[RegisteredTool]", list(tools))


def _text_response(content: str) -> AgentLLMResponse:
    return AgentLLMResponse(content=content, tool_calls=[], raw_content=None)


def _tool_call_response(
    call_id: str,
    name: str,
    tool_input: dict[str, Any] | None = None,
) -> AgentLLMResponse:
    return AgentLLMResponse(
        content="",
        tool_calls=[ToolCall(id=call_id, name=name, input=dict(tool_input or {}))],
        raw_content=None,
    )


def _agent(
    llm: FakeLLM,
    tools: list[Any],
    max_iterations: int = 5,
    max_stagnant_iterations: int | None = None,
    on_event: Any = None,
    on_runtime_event: Any = None,
) -> Agent:
    return Agent(
        llm=llm,
        system="sys",
        tools=tools,
        resolved_integrations={},
        max_iterations=max_iterations,
        max_stagnant_iterations=max_stagnant_iterations,
        on_event=on_event,
        on_runtime_event=on_runtime_event,
    )


def test_agent_exposes_headless_agent_entrypoint() -> None:
    from core.agent_harness.turns.headless_adapters import NullToolProvider

    llm = FakeLLM(iter([_text_response("hello from headless")]))
    agent = InMemoryHeadlessBuild().agent(
        tools=NullToolProvider(),
        llm_factory=lambda: llm,
    )
    result = agent.dispatch("hello")

    assert result.assistant_response_text == "hello from headless"


def test_one_headless_agent_dispatches_multiple_messages() -> None:
    """Configure once, dispatch many: both turns run on the same agent and session."""

    from core.agent_harness.turns.headless_adapters import NullToolProvider

    llm = FakeLLM(iter([_text_response("hello from headless"), _text_response("hello again")]))
    agent = InMemoryHeadlessBuild().agent(
        tools=NullToolProvider(),
        llm_factory=lambda: llm,
    )
    first = agent.dispatch("one")
    second = agent.dispatch("two")

    assert first.assistant_response_text == "hello from headless"
    assert second.assistant_response_text == "hello again"
    # Both turns landed on the same shared session — reuse, not a fresh store per call.
    assert len(agent._session.cli_agent_messages) == 4


def test_provided_accounting_is_consumed_once() -> None:
    """Bound accounting is take-once — hosts must rebind per message."""
    from core.agent_harness.turns.headless_adapters import NoopTurnAccounting, NullToolProvider

    accounting = NoopTurnAccounting()
    agent = InMemoryHeadlessBuild().agent(tools=NullToolProvider())
    agent.bind_turn(TurnBinding(accounting=accounting))
    assert agent._take_accounting("a") is accounting
    # Slot cleared so a forgotten bind_turn cannot leak the prior turn's prompt.
    assert agent._take_accounting("b") is not accounting


def test_default_accounting_is_resolved_fresh_per_message() -> None:
    from core.agent_harness.accounting.turn_accounting import DefaultTurnAccounting
    from core.agent_harness.turns.headless_adapters import InMemorySessionState, NullToolProvider

    class _PersistentState(InMemorySessionState):
        store = object()  # persistent-backed session selects DefaultTurnAccounting

    agent = InMemoryHeadlessBuild(session=_PersistentState()).agent(tools=NullToolProvider())

    first = agent._take_accounting("msg-a")
    second = agent._take_accounting("msg-b")
    assert isinstance(first, DefaultTurnAccounting)
    assert isinstance(second, DefaultTurnAccounting)
    assert first is not second  # resolved per message, not once at construction


def test_immediate_final_answer_executes_no_tools() -> None:
    llm = FakeLLM(iter([_text_response("done immediately")]))

    result = _agent(llm, _tools(FakeTool("query_logs"))).run([{"role": "user", "content": "hello"}])

    assert isinstance(result, AgentRunResult)
    assert result.executed == []
    assert result.final_text == "done immediately"
    assert result.hit_iteration_cap is False


def test_run_records_final_system_prompt() -> None:
    llm = FakeLLM(iter([_text_response("done")]))

    result = _agent(llm, _tools(FakeTool("query_logs"))).run([{"role": "user", "content": "hello"}])

    assert result.final_system_prompt == "sys"


def test_run_records_system_prompt_edited_by_before_provider_request_hook() -> None:
    llm = FakeLLM(iter([_text_response("done")]))
    agent = Agent(
        llm=llm,
        system="sys",
        tools=[],
        resolved_integrations={},
        max_iterations=1,
        provider_hooks=ProviderHooks(
            before_provider_request=lambda request: replace(
                request, system=request.system + " [edited]"
            )
        ),
    )

    result = agent.run([{"role": "user", "content": "hello"}])

    assert result.final_system_prompt == "sys [edited]"


def test_transform_messages_hook_filters_context_sent_to_llm() -> None:
    llm = FakeLLM(iter([_text_response("done")]))
    agent = Agent(
        llm=llm,
        system="sys",
        tools=[],
        resolved_integrations={},
        max_iterations=1,
        provider_hooks=ProviderHooks(transform_messages=lambda messages: list(messages)[-1:]),
    )

    agent.run(
        [
            {"role": "user", "content": "first"},
            {"role": "user", "content": "second"},
        ]
    )

    assert llm.invocations == 1
    assert len(llm.seen_messages[0]) == 1
    assert llm.seen_messages[0][0]["content"] == "second"


def test_convert_to_llm_hook_replaces_default_message_conversion() -> None:
    llm = FakeLLM(iter([_text_response("done")]))

    def stamp(_llm: Any, messages: Any) -> list[dict[str, Any]]:
        return [{"role": "user", "content": f"converted:{m.content}"} for m in messages]

    agent = Agent(
        llm=llm,
        system="sys",
        tools=[],
        resolved_integrations={},
        max_iterations=1,
        provider_hooks=ProviderHooks(convert_to_llm=stamp),
    )

    agent.run([{"role": "user", "content": "hello"}])

    assert llm.invocations == 1
    assert llm.seen_messages[0][0]["content"] == "converted:hello"


def test_after_response_hook_can_rewrite_llm_reply() -> None:
    llm = FakeLLM(iter([_text_response("original")]))
    agent = Agent(
        llm=llm,
        system="sys",
        tools=[],
        resolved_integrations={},
        max_iterations=1,
        provider_hooks=ProviderHooks(
            after_provider_response=lambda _req, resp: replace(resp, content="rewritten")
        ),
    )

    result = agent.run([{"role": "user", "content": "hi"}])

    assert llm.invocations == 1
    assert result.final_text == "rewritten"


def test_one_tool_round_then_final() -> None:
    output = {"value": 42}
    llm = FakeLLM(
        iter(
            [
                _tool_call_response("c1", "query_logs"),
                _text_response("here is the answer"),
            ]
        )
    )
    initial: list[dict[str, Any]] = [{"role": "user", "content": "hello"}]

    result = _agent(llm, _tools(FakeTool("query_logs", output))).run(initial)

    assert len(result.executed) == 1
    tc, tool_output = result.executed[0]
    assert isinstance(tc, ToolCall)
    assert tc.name == "query_logs"
    assert tool_output == output
    assert result.final_text == "here is the answer"
    assert result.hit_iteration_cap is False
    # user + assistant(tool call) + tool-result + assistant(final)
    assert len(result.messages) == 4
    assert isinstance(result.messages[0], UserRuntimeMessage)
    assert result.messages[0].content == initial[0]["content"]
    assert isinstance(result.messages[2], ToolResultRuntimeMessage)
    assert llm.seen_messages[0] == initial


def test_generic_tool_result_conversion_does_not_import_litellm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generic/static clients should not pay LiteLLM's cold import cost."""
    real_import = builtins.__import__

    def guarded_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "core.llm.transports.litellm.clients" or name.startswith("litellm"):
            raise AssertionError(f"unexpected LiteLLM import: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    llm = FakeLLM(iter(()))
    call = ToolCall(id="c1", name="query_logs", input={})
    message = ToolResultRuntimeMessage(tool_calls=(call,), results=({"ok": True},))

    assert MessageMapper(llm).to_provider_messages([message]) == [
        {
            "role": "tool",
            "results": [{"id": "c1", "output": {"ok": True}}],
        }
    ]


def test_agent_transcript_can_keep_app_messages_out_of_provider_context() -> None:
    llm = FakeLLM(iter([_text_response("done")]))

    result = _agent(llm, _tools(FakeTool("query_logs"))).run(
        [
            UserRuntimeMessage(content="hello"),
            AppRuntimeMessage("ui-note", "render only", include_in_context=False),
            AppRuntimeMessage("runtime-context", "visible context"),
        ]
    )

    assert result.final_text == "done"
    assert [message["content"] for message in llm.seen_messages[0]] == [
        "hello",
        "visible context",
    ]
    assert len(result.messages) == 4


def test_agent_excludes_unrecognized_provider_dict_roles_from_llm_context() -> None:
    llm = FakeLLM(iter([_text_response("done")]))

    result = _agent(llm, _tools(FakeTool("query_logs"))).run(
        [
            {"role": "unknown", "content": "skip"},
            {"role": "user", "content": "hello"},
        ]
    )

    assert result.final_text == "done"
    assert llm.seen_messages[0] == [{"role": "user", "content": "hello"}]


def test_legacy_text_blocks_convert_to_bedrock_converse_content() -> None:
    from core.llm.transports.sdk.agent_clients import BedrockConverseAgentClient

    llm = BedrockConverseAgentClient.__new__(BedrockConverseAgentClient)
    messages = [AppRuntimeMessage("custom", [{"type": "text", "text": "custom note"}])]

    assert MessageMapper(llm).to_provider_messages(messages) == [
        {"role": "user", "content": [{"text": "custom note"}]}
    ]


def test_runtime_events_emit_typed_lifecycle_and_streaming_order() -> None:
    llm = FakeLLM(
        iter(
            [
                _tool_call_response("c1", "query_logs"),
                _text_response("final"),
            ]
        )
    )
    events: list[RuntimeEvent] = []

    _agent(llm, _tools(FakeTool("query_logs")), on_runtime_event=events.append).run(
        [{"role": "user", "content": "hello"}]
    )

    assert [event.type for event in events] == [
        "agent_start",
        "turn_start",
        "provider_request_start",
        "provider_request_end",
        "message_start",
        "tool_execution_start",
        "tool_execution_end",
        "turn_end",
        "turn_start",
        "provider_request_start",
        "provider_request_end",
        "message_start",
        "message_update",
        "turn_end",
        "agent_end",
    ]
    message_updates = [event for event in events if isinstance(event, MessageUpdateEvent)]
    assert [event.delta for event in message_updates] == ["final"]


def test_message_update_flags_tool_call_iterations() -> None:
    """Intermediate commentary carries has_tool_calls=True; the final answer False."""
    llm = FakeLLM(
        iter(
            [
                AgentLLMResponse(
                    content="### [1/2] Checking logs",
                    tool_calls=[ToolCall(id="c1", name="query_logs", input={})],
                    raw_content=None,
                ),
                _text_response("final"),
            ]
        )
    )
    events: list[RuntimeEvent] = []

    _agent(llm, _tools(FakeTool("query_logs")), on_runtime_event=events.append).run(
        [{"role": "user", "content": "hello"}]
    )

    updates = [event for event in events if isinstance(event, MessageUpdateEvent)]
    assert [(event.delta, event.data["has_tool_calls"]) for event in updates] == [
        ("### [1/2] Checking logs", True),
        ("final", False),
    ]


def test_legacy_on_event_bridge_emits_kinds_in_order() -> None:
    llm = FakeLLM(
        iter(
            [
                _tool_call_response("c1", "query_logs"),
                _text_response("final"),
            ]
        )
    )
    events: list[str] = []

    def on_event(kind: str, _data: dict[str, Any]) -> None:
        events.append(kind)

    _agent(llm, _tools(FakeTool("query_logs")), on_event=on_event).run(
        [{"role": "user", "content": "hello"}]
    )

    assert events == [
        "agent_start",
        "llm_start",
        "tool_start",
        "tool_end",
        "llm_start",
        "message_update",
        "agent_end",
    ]


def test_on_event_failure_is_logged_and_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    llm = FakeLLM(iter([_text_response("final")]))

    def on_event(_kind: str, _data: dict[str, Any]) -> None:
        raise RuntimeError("broken renderer")

    with caplog.at_level(logging.DEBUG, logger="core.agent.mixins"):
        result = _agent(llm, _tools(FakeTool("query_logs")), on_event=on_event).run(
            [{"role": "user", "content": "hello"}]
        )

    assert result.final_text == "final"
    assert "[runtime] on_event(agent_start) raised; ignoring" in caplog.text


def test_steer_injects_message_before_next_llm_turn() -> None:
    llm = FakeLLM(iter([_text_response("final")]))
    agent = _agent(llm, _tools(FakeTool("query_logs")))

    agent.steer("look at the newest deploy first")
    result = agent.run([{"role": "user", "content": "hello"}])

    assert result.final_text == "final"
    assert [message["content"] for message in llm.seen_messages[0]] == [
        "hello",
        "look at the newest deploy first",
    ]


def test_follow_up_runs_after_an_accepted_final_answer() -> None:
    llm = FakeLLM(iter([_text_response("first answer"), _text_response("follow-up answer")]))
    agent = _agent(llm, _tools(FakeTool("query_logs")), max_iterations=3)

    agent.follow_up("now summarize the remediation")
    result = agent.run([{"role": "user", "content": "hello"}])

    assert result.final_text == "follow-up answer"
    assert llm.invocations == 2
    assert [message["content"] for message in llm.seen_messages[1]] == [
        "hello",
        "first answer",
        "now summarize the remediation",
    ]


def test_agent_tool_context_update_emits_tool_execution_update() -> None:
    def execute(_payload: dict[str, Any], context: AgentToolContext) -> dict[str, Any]:
        assert context.on_update is not None
        context.on_update({"status": "halfway"})
        return {"ok": True}

    tool = AgentTool(
        name="agent_tool",
        description="test tool",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        execute=execute,
    )
    llm = FakeLLM(iter([_tool_call_response("c1", "agent_tool"), _text_response("done")]))
    events: list[RuntimeEvent] = []

    result = _agent(llm, [tool], on_runtime_event=events.append).run(
        [{"role": "user", "content": "hello"}]
    )

    assert result.final_text == "done"
    updates = [event for event in events if isinstance(event, ToolExecutionUpdateEvent)]
    assert len(updates) == 1
    assert updates[0].tool_call_id == "c1"
    assert updates[0].tool_name == "agent_tool"
    assert updates[0].partial_result == {"status": "halfway"}


def test_tool_filtering_runs_after_subclass_initialization() -> None:
    class LateStateFilteringAgent(Agent[RegisteredTool]):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.allowed_tool_names = {"keep"}

        def _filter_tools(self, tools: list[RegisteredTool]) -> list[RegisteredTool]:
            return [tool for tool in tools if tool.name in self.allowed_tool_names]

    output = {"value": 42}
    llm = FakeLLM(
        iter(
            [
                _tool_call_response("c1", "keep"),
                _text_response("done"),
            ]
        )
    )
    agent = LateStateFilteringAgent(
        llm=llm,
        system="sys",
        tools=_tools(FakeTool("drop"), FakeTool("keep", output)),
        resolved_integrations={},
        max_iterations=3,
    )

    result = agent.run([{"role": "user", "content": "hello"}])

    assert llm.schema_tool_names == [["keep"]]
    assert [(tc.name, tool_output) for tc, tool_output in result.executed] == [("keep", output)]


def test_iteration_cap_gets_one_tool_disabled_final_handoff() -> None:
    max_iterations = 3
    llm = FakeLLM(
        iter(
            [
                _tool_call_response(f"c{step}", "query_logs", {"step": step})
                for step in range(max_iterations)
            ]
            + [_text_response("I completed two checks; the remaining work is blocked.")]
        )
    )

    result = _agent(llm, _tools(FakeTool("query_logs")), max_iterations=max_iterations).run(
        [{"role": "user", "content": "hello"}]
    )

    assert result.hit_iteration_cap is True
    assert result.llm_iterations_used == max_iterations
    assert len(result.executed) == max_iterations
    assert result.final_text == "I completed two checks; the remaining work is blocked."
    assert llm.invocations == max_iterations + 1
    assert llm.seen_tools[-1] == []


def test_productive_tool_run_can_continue_past_thirteen_iterations() -> None:
    tool_iterations = 14
    llm = FakeLLM(
        iter(
            [
                _tool_call_response(f"c{step}", "query_logs", {"step": step})
                for step in range(tool_iterations)
            ]
            + [_text_response("all steps completed")]
        )
    )

    result = _agent(
        llm,
        _tools(FakeTool("query_logs")),
        max_iterations=20,
        max_stagnant_iterations=3,
    ).run([{"role": "user", "content": "run every step"}])

    assert result.hit_iteration_cap is False
    assert result.llm_iterations_used == tool_iterations + 1
    assert len(result.executed) == tool_iterations
    assert result.final_text == "all steps completed"


def test_repeated_observations_stop_early_with_one_tool_disabled_handoff() -> None:
    llm = FakeLLM(
        iter(
            [_tool_call_response(f"c{step}", "query_logs") for step in range(4)]
            + [_text_response("The same query failed repeatedly; use a narrower time range.")]
        )
    )

    result = _agent(
        llm,
        _tools(FakeTool("query_logs", {"error": "unavailable"})),
        max_iterations=20,
        max_stagnant_iterations=3,
    ).run([{"role": "user", "content": "keep trying"}])

    assert result.hit_iteration_cap is True
    assert result.llm_iterations_used == 4
    assert len(result.executed) == 4
    assert result.final_text == "The same query failed repeatedly; use a narrower time range."
    assert llm.invocations == 5
    assert llm.seen_tools[-1] == []


def test_failed_safety_handoff_uses_deterministic_final_text() -> None:
    def responses() -> Iterator[AgentLLMResponse]:
        yield _tool_call_response("c1", "query_logs")
        raise RuntimeError("final sampling unavailable")

    llm = FakeLLM(responses())

    result = _agent(llm, _tools(FakeTool("query_logs")), max_iterations=1).run(
        [{"role": "user", "content": "finish this"}]
    )

    assert result.hit_iteration_cap is True
    assert "emergency tool-iteration ceiling" in result.final_text
    assert llm.invocations == 2
    assert llm.seen_tools[-1] == []


def test_react_loop_records_partial_iterations_when_llm_raises() -> None:
    def responses() -> Iterator[AgentLLMResponse]:
        yield _tool_call_response("c1", "query_logs")
        yield _tool_call_response("c2", "query_logs")
        raise RuntimeError("provider down")

    llm = FakeLLM(responses())
    agent = _agent(llm, _tools(FakeTool("query_logs")), max_iterations=5)

    with pytest.raises(RuntimeError, match="provider down"):
        agent.run([{"role": "user", "content": "hello"}])

    assert agent._react_iterations_used == 3


def test_run_sums_provider_reported_usage_across_model_calls() -> None:
    # Arrange: two model calls, each reporting exact usage.
    first = replace(_tool_call_response("c1", "query_logs"), input_tokens=120, output_tokens=30)
    second = replace(_text_response("done"), input_tokens=200, output_tokens=50)
    llm = FakeLLM(iter([first, second]))

    # Act
    result = _agent(llm, _tools(FakeTool("query_logs", {"ok": True}))).run(
        [{"role": "user", "content": "hello"}]
    )

    # Assert: the run carries the totals the host records on the session.
    assert (result.input_tokens, result.output_tokens) == (320, 80)
