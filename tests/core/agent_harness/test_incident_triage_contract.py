"""Incident-triage contracts for the chat-only agent.

The scenarios use deterministic tools and an LLM double so normal CI can pin
the production ReAct loop without credentials or live vendor dependencies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from core.agent_harness.turns.headless_adapters import (
    BufferOutputSink,
    InMemorySessionState,
)
from core.agent_harness.turns.headless_build import InMemoryHeadlessBuild
from core.agent_harness.turns.turn_results import TurnResult
from core.llm.types import AgentLLMResponse, ToolCall
from core.tool.contracts import RegisteredTool, SideEffectLevel

_INCIDENT_PROMPT = "Investigate why checkout-api latency increased after the latest deployment."
_PASTED_ALERT = (
    "ALERT checkout-api p99 latency above 2s in prod at 10:42 UTC. "
    "Investigate and report what the connected evidence supports."
)


def _response(*, text: str = "", tool: str | None = None) -> AgentLLMResponse:
    calls = [] if tool is None else [ToolCall(id=f"call-{tool}", name=tool, input={})]
    return AgentLLMResponse(content=text, tool_calls=calls, raw_content=None)


@dataclass
class _IncidentLLM:
    """Select read-only evidence tools, then report only observed values."""

    tools_to_call: list[str]
    final_text: str
    invocations: int = 0
    offered_tools: list[list[str]] = field(default_factory=list)
    seen_messages: list[list[dict[str, Any]]] = field(default_factory=list)
    model_id: str = "deterministic-incident-contract"

    def tool_schemas(self, tools: list[Any]) -> list[dict[str, Any]]:
        names = [str(tool.name) for tool in tools]
        self.offered_tools.append(names)
        return [{"name": name} for name in names]

    def invoke(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AgentLLMResponse:
        _ = system
        self.invocations += 1
        self.seen_messages.append(messages)
        if tools == []:
            return _response(text=self.final_text)
        completed_calls = sum(1 for message in messages if message.get("role") == "tool")
        if completed_calls < len(self.tools_to_call):
            return _response(tool=self.tools_to_call[completed_calls])
        return _response(text=self.final_text)

    @staticmethod
    def build_assistant_message(content: str, tool_calls: list[ToolCall]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"id": call.id, "name": call.name, "input": call.input} for call in tool_calls
            ],
        }

    @staticmethod
    def build_tool_result_message(
        tool_calls: list[ToolCall],
        results: list[Any],
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "results": [
                {"id": call.id, "name": call.name, "output": result}
                for call, result in zip(tool_calls, results)
            ],
        }


class _ToolProvider:
    def __init__(self, tools: list[RegisteredTool], *, cancelled: bool = False) -> None:
        self._tools = tools
        self._cancelled = cancelled

    def action_tools(self, **_kwargs: Any) -> list[RegisteredTool]:
        return list(self._tools)

    def tool_resources(self) -> dict[str, Any]:
        console = type("ContractConsole", (), {"cancel_requested": self._cancelled})()
        context = type("ContractContext", (), {"console": console})()
        return {"action_tool_context": context}

    def observer(self, *, message: str) -> Any:
        _ = message

        def _observe(_kind: str, _data: dict[str, Any]) -> None:
            return None

        return _observe


def _tool(name: str, result: dict[str, Any]) -> RegisteredTool:
    def _run() -> dict[str, Any]:
        return result

    return RegisteredTool(
        name=name,
        description=f"Read deterministic {name} evidence.",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        source="incident-contract",
        run=_run,
        side_effect_level=SideEffectLevel.READ_ONLY,
    )


def _run(
    prompt: str,
    llm: _IncidentLLM,
    tools: list[RegisteredTool],
    *,
    cancelled: bool = False,
) -> TurnResult:
    session = InMemorySessionState(
        configured_integrations=["incident-contract"],
        configured_integrations_known=True,
    )
    output = BufferOutputSink()
    agent = InMemoryHeadlessBuild(session=session, output=output).agent(
        tools=_ToolProvider(tools, cancelled=cancelled),
        llm_factory=lambda: llm,
    )
    return agent.dispatch(prompt)


def test_natural_incident_prompt_correlates_connected_read_only_evidence() -> None:
    evidence = {
        "query_deployments": {"revision": "checkout-v184", "deployed_at": "10:39 UTC"},
        "query_metrics": {"p99_ms": 2310, "baseline_ms": 410, "started_at": "10:41 UTC"},
        "query_logs": {
            "message": "upstream timeout",
            "count": 187,
            "started_at": "10:41 UTC",
        },
    }
    conclusion = (
        "checkout-v184 deployed at 10:39 UTC. At 10:41 UTC p99 rose from 410ms "
        "to 2310ms alongside 187 upstream timeout errors. The deployment is the "
        "leading correlated cause; roll it back and verify latency recovery."
    )
    llm = _IncidentLLM(list(evidence), conclusion)

    result = _run(
        _INCIDENT_PROMPT,
        llm,
        [_tool(name, payload) for name, payload in evidence.items()],
    )

    assert llm.offered_tools[0] == list(evidence)
    observed = json.dumps(llm.seen_messages[-1], default=str)
    assert all(str(value) in observed for value in ("checkout-v184", 2310, 410, 187))
    assert all(value in conclusion for value in ("checkout-v184", "2310ms", "410ms", "187"))
    assert result.action_result.executed_count == 3
    assert result.action_result.executed_success_count == 3
    assert result.primary_response_text.startswith(conclusion)


def test_unavailable_integration_repetition_stops_with_an_explicit_limitation() -> None:
    limitation = (
        "The log integration is not available, so I cannot verify log evidence. "
        "No log-derived root cause can be claimed from the connected data."
    )
    llm = _IncidentLLM(["query_logs"] * 10, limitation)

    result = _run(_INCIDENT_PROMPT, llm, [_tool("query_metrics", {"p99_ms": 2310})])

    assert result.action_result.hit_iteration_cap is True
    assert result.final_intent == "agent_incomplete"
    assert llm.invocations == 5
    assert "not available" in result.primary_response_text.lower()
    assert "cannot verify log evidence" in result.primary_response_text
    assert "root cause is" not in result.primary_response_text.lower()


def test_pasted_alert_keeps_its_incident_head_within_the_prompt_budget() -> None:
    llm = _IncidentLLM([], "The alert is understood; no evidence tools are connected.")

    result = _run(f"{_PASTED_ALERT} {'x' * 2_000}", llm, [])

    user_message = str(llm.seen_messages[0][0]["content"])
    assert _PASTED_ALERT in user_message
    assert "x" * 600 not in user_message
    assert "no evidence tools are connected" in result.primary_response_text


def test_cancelled_incident_turn_stops_before_model_or_tool_work() -> None:
    llm = _IncidentLLM(["query_logs"], "must not be returned")

    result = _run(
        _INCIDENT_PROMPT,
        llm,
        [_tool("query_logs", {"error": "must not run"})],
        cancelled=True,
    )

    assert result.cancelled is True
    assert result.primary_response_text == ""
    assert llm.invocations == 0
