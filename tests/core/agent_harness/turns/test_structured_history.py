"""Earlier turns reach the model as typed messages, with their tool calls and results."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from config.constants.conversation_history import OPENSRE_HISTORY_TOOL_RESULT_CHARS_ENV
from core.agent_harness.turns.headless_adapters import NullToolProvider
from core.agent_harness.turns.headless_build import InMemoryHeadlessBuild
from core.agent_harness.turns.structured_history import (
    build_turn_evidence,
    history_messages,
    tool_items_from_run,
)
from core.llm.shared.openai_responses import responses_input
from core.llm.transports.sdk.agent_clients import (
    AnthropicAgentClient,
    BedrockConverseAgentClient,
    OpenAIAgentClient,
)
from core.llm.types import AgentLLMResponse, ToolCall
from core.messages import (
    AssistantRuntimeMessage,
    MessageMapper,
    ToolResultRuntimeMessage,
    UserRuntimeMessage,
)


class _ScriptedLLM:
    """Generic (non-provider) client that records every request it receives."""

    model_id = "scripted"

    def __init__(self, responses: Iterator[AgentLLMResponse]) -> None:
        self._responses = responses
        self.requests: list[list[dict[str, Any]]] = []

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        return [{"name": tool.name} for tool in tools]

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = (system, tools)
        self.requests.append(list(messages))
        return next(self._responses)

    def build_assistant_message(self, content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [{"id": call.id, "name": call.name} for call in tool_calls],
        }

    def build_tool_result_message(
        self, tool_calls: list[ToolCall], results: list[Any]
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "results": [
                {"id": call.id, "output": output}
                for call, output in zip(tool_calls, results, strict=True)
            ],
        }


class _CiRunsTool:
    name = "ci_runs"

    def validate_public_input(self, value: dict[str, Any]) -> str | None:
        _ = value
        return None

    def extract_params(self, resolved: dict[str, Any]) -> dict[str, Any]:
        _ = resolved
        return {}

    def run(self, **kwargs: Any) -> dict[str, Any]:
        _ = kwargs
        return {"failing_run_id": "9312", "workflow": "integration-tests"}


class _OneToolProvider(NullToolProvider):
    def action_tools(self, **_kwargs: Any) -> list[Any]:
        return [_CiRunsTool()]


def _text(content: str) -> AgentLLMResponse:
    return AgentLLMResponse(content=content, tool_calls=[], raw_content=None)


def _call(call_id: str, name: str) -> AgentLLMResponse:
    return AgentLLMResponse(
        content="", tool_calls=[ToolCall(id=call_id, name=name, input={})], raw_content=None
    )


def test_a_follow_up_sees_the_earlier_tool_call_and_its_result() -> None:
    llm = _ScriptedLLM(
        iter(
            [
                _call("call_1", "ci_runs"),
                _text("One run is failing."),
                _text("Rerunning it."),
            ]
        )
    )
    agent = InMemoryHeadlessBuild().agent(tools=_OneToolProvider(), llm_factory=lambda: llm)

    agent.dispatch("Is CI failing?")
    agent.dispatch("Rerun the one that failed")

    second_turn = llm.requests[-1]
    roles = [message["role"] for message in second_turn]
    assert roles == ["user", "assistant", "tool", "assistant", "user"]
    assert second_turn[0]["content"] == "Is CI failing?"
    assert second_turn[1]["tool_calls"] == [{"id": "call_1", "name": "ci_runs"}]
    # The exact value the follow-up refers to arrives as the tool's own output.
    assert "9312" in str(second_turn[2]["results"][0]["output"])
    assert "One run is failing." in second_turn[3]["content"]
    current = second_turn[4]["content"]
    assert current.startswith("USER MESSAGE (literal): <<<Rerun the one that failed>>>")
    assert "RECENT CONVERSATION" not in current


def _recorded_turn() -> list[Any]:
    return [
        UserRuntimeMessage(content="history"),
        UserRuntimeMessage(content="USER MESSAGE (literal): <<<Is CI failing?>>>"),
        AssistantRuntimeMessage(
            content="Checking.",
            tool_calls=(ToolCall(id="toolu.1/x", name="ci_runs", input={"repo": "o/r"}),),
        ),
        ToolResultRuntimeMessage(
            tool_calls=(ToolCall(id="toolu.1/x", name="ci_runs", input={}),),
            results=('{"failing_run_id": "9312"}',),
        ),
        UserRuntimeMessage(content="host nudge: keep going"),
        AssistantRuntimeMessage(content="One run is failing."),
    ]


def test_recorded_items_skip_history_and_host_nudges_and_keep_call_pairs() -> None:
    items = tool_items_from_run(_recorded_turn(), history_count=1)

    assert [item["kind"] for item in items] == ["assistant", "tool_results"]
    [call] = items[0]["tool_calls"]
    # Ids are normalized once, so the call and its result still match on replay.
    assert call["id"] == "toolu_1_x"
    assert items[1]["results"][0]["id"] == "toolu_1_x"


def test_long_tool_output_keeps_its_head_and_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENSRE_HISTORY_TOOL_RESULT_CHARS_ENV, "1000")
    output = "HEAD " + "x" * 5_000 + " TAIL"
    messages = [
        UserRuntimeMessage(content="now"),
        AssistantRuntimeMessage(content="", tool_calls=(ToolCall(id="c1", name="logs", input={}),)),
        ToolResultRuntimeMessage(
            tool_calls=(ToolCall(id="c1", name="logs", input={}),), results=(output,)
        ),
    ]

    [_, results] = tool_items_from_run(messages, history_count=0)
    content = results["results"][0]["content"]

    assert len(content) <= 1000
    assert content.startswith("HEAD ")
    assert content.endswith(" TAIL")
    assert "characters omitted" in content


def _history() -> list[Any]:
    items = tool_items_from_run(_recorded_turn(), history_count=1)
    evidence = build_turn_evidence("Is CI failing?", "One run is failing.", items)
    transcript = [("user", "Is CI failing?"), ("assistant", "One run is failing.")]
    return history_messages(transcript, [evidence])


def test_replay_builds_valid_anthropic_tool_turns() -> None:
    llm = AnthropicAgentClient(model="claude-sonnet-4-6", client=object())

    provider = MessageMapper(llm).to_provider_messages(_history())

    assert [message["role"] for message in provider] == ["user", "assistant", "user", "assistant"]
    assistant_blocks = provider[1]["content"]
    assert assistant_blocks[0] == {"type": "text", "text": "Checking."}
    assert assistant_blocks[1]["type"] == "tool_use"
    [result] = provider[2]["content"]
    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == assistant_blocks[1]["id"]


def test_replay_builds_valid_bedrock_tool_turns() -> None:
    llm = object.__new__(BedrockConverseAgentClient)

    provider = MessageMapper(llm).to_provider_messages(_history())

    tool_use = provider[1]["content"][1]["toolUse"]
    tool_result = provider[2]["content"][0]["toolResult"]
    assert provider[1]["content"][0] == {"text": "Checking."}
    assert tool_result["toolUseId"] == tool_use["toolUseId"]


def test_replay_builds_paired_openai_responses_items() -> None:
    llm = OpenAIAgentClient(model="gpt-5.6-sol", credential_resolver=lambda _name: "test-key")

    items = responses_input(MessageMapper(llm).to_provider_messages(_history()))

    calls = [item for item in items if item.get("type") == "function_call"]
    outputs = [item for item in items if item.get("type") == "function_call_output"]
    assert [call["call_id"] for call in calls] == [output["call_id"] for output in outputs]
    assert "9312" in outputs[0]["output"]


def test_a_transcript_rewritten_elsewhere_replays_as_text() -> None:
    evidence = build_turn_evidence("Is CI failing?", "One run is failing.", ())
    seeded = [("user", "From a chat thread"), ("assistant", "A reply the evidence never saw")]

    replayed = history_messages(seeded, [evidence])

    assert [(type(message).__name__, message.content) for message in replayed] == [
        ("UserRuntimeMessage", "From a chat thread"),
        ("AssistantRuntimeMessage", "A reply the evidence never saw"),
    ]


def test_an_earlier_turns_outcome_report_is_not_shown_again() -> None:
    from types import SimpleNamespace

    from core.agent_harness.turns.action_driver import _latest_unshown_outcome_report

    earlier = AssistantRuntimeMessage(content="- **Outcome:** PR #12 repaired and pushed.")
    result = SimpleNamespace(
        messages=[
            UserRuntimeMessage(content="repair PR 12"),
            earlier,
            UserRuntimeMessage(content="USER MESSAGE (literal): <<<thanks>>>"),
            AssistantRuntimeMessage(content="You're welcome."),
        ]
    )

    assert _latest_unshown_outcome_report(result, "You're welcome.", (), history_count=2) == ""
    # Without the history boundary the old report would resurface as this turn's.
    assert _latest_unshown_outcome_report(result, "You're welcome.", ()) != ""


def test_evidence_needs_the_same_user_message_not_only_the_same_reply() -> None:
    evidence = build_turn_evidence("deploy api", "Done.", ())
    replaced = [("user", "restart the worker"), ("assistant", "Done.")]

    replayed = history_messages(replaced, [evidence])

    assert [message.content for message in replayed] == ["restart the worker", "Done."]


def test_a_restored_bare_yes_still_finds_its_evidence() -> None:
    items = tool_items_from_run(_recorded_turn(), history_count=1)
    evidence = build_turn_evidence(
        "Yes — please run the CI check.", "One run is failing.", items, typed_text="yes"
    )
    # The session log keeps the message as typed; the live transcript kept its expansion.
    restored = [("user", "yes"), ("assistant", "One run is failing.")]

    replayed = history_messages(restored, [evidence])

    assert any(isinstance(message, ToolResultRuntimeMessage) for message in replayed)
