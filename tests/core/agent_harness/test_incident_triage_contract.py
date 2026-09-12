"""Incident-triage contracts for the chat-only agent.

The scenarios use deterministic tools and an LLM double so normal CI can pin
the production ReAct loop without credentials or live vendor dependencies.
"""

from __future__ import annotations

import json
from contextlib import suppress
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
    """Rule model that selects tools from the request and answers from observations."""

    invocations: int = 0
    offered_tools: list[list[str]] = field(default_factory=list)
    seen_messages: list[list[dict[str, Any]]] = field(default_factory=list)
    closing_text: str = ""
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
        self.invocations += 1
        self.seen_messages.append(messages)
        if system and "You review whether an agent completed" in system:
            return _response(text='{"verdict":"GOAL_REACHED"}')

        request = str(messages[0].get("content", "")).lower()
        if "checkout-api" not in request or not ({"latency", "p99"} & set(request.split())):
            return _response(text="No incident-triage request was provided.")

        observations = self._observations(messages)
        if tools == []:
            return _response(text=self._answer(observations))

        available = {str(schema.get("name")) for schema in tools or [] if isinstance(schema, dict)}
        wanted = []
        if "deploy" in request:
            wanted.append("query_deployments")
        if "latency" in request or "p99" in request:
            wanted.append("query_metrics")
        wanted.append("query_logs")
        for name in wanted:
            if name not in observations:
                if name in available or name == "query_logs":
                    return _response(tool=name)
                continue
            if name == "query_logs" and "not available" in str(observations[name]).lower():
                return _response(tool=name)
        return _response(text=self._answer(observations))

    @staticmethod
    def _observations(messages: list[dict[str, Any]]) -> dict[str, Any]:
        observed: dict[str, Any] = {}
        for message in messages:
            if message.get("role") != "tool":
                continue
            for item in message.get("results", []):
                if isinstance(item, dict):
                    output = item.get("output")
                    if isinstance(output, str):
                        with suppress(json.JSONDecodeError):
                            output = json.loads(output)
                    observed[str(item.get("name"))] = output
        return observed

    def _answer(self, observations: dict[str, Any]) -> str:
        unavailable = str(observations.get("query_logs", "")).lower()
        if "not available" in unavailable:
            self.closing_text = (
                "The log integration is not available, so I cannot verify log evidence. "
                "No log-derived root cause can be claimed from the connected data."
            )
            return self.closing_text
        if not observations:
            self.closing_text = (
                "No evidence tools are connected, so the alert cannot be verified yet."
            )
            return self.closing_text

        deployment = observations["query_deployments"]
        metrics = observations["query_metrics"]
        logs = observations["query_logs"]
        self.closing_text = (
            f"{deployment['revision']} deployed at {deployment['deployed_at']}. "
            f"At {metrics['started_at']} p99 rose from {metrics['baseline_ms']}ms to "
            f"{metrics['p99_ms']}ms alongside {logs['count']} {logs['message']} errors. "
            "The deployment is the leading correlated cause; roll it back and verify "
            "latency recovery."
        )
        return self.closing_text

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
    llm = _IncidentLLM()

    result = _run(
        _INCIDENT_PROMPT,
        llm,
        [_tool(name, payload) for name, payload in evidence.items()],
    )

    assert llm.offered_tools[0] == list(evidence)
    assert all(value in llm.closing_text for value in ("checkout-v184", "2310ms", "410ms", "187"))
    assert result.action_result.executed_count == 3
    assert result.action_result.executed_success_count == 3
    assert result.primary_response_text.startswith(llm.closing_text)


def test_unavailable_integration_repetition_stops_with_an_explicit_limitation() -> None:
    llm = _IncidentLLM()

    result = _run(
        _INCIDENT_PROMPT,
        llm,
        [
            _tool("query_deployments", {"revision": "checkout-v184"}),
            _tool("query_metrics", {"p99_ms": 2310}),
        ],
    )

    assert result.action_result.hit_iteration_cap is True
    assert result.final_intent == "agent_incomplete"
    assert llm.invocations < 10
    assert "not available" in result.primary_response_text.lower()
    assert "cannot verify log evidence" in result.primary_response_text
    assert "root cause is" not in result.primary_response_text.lower()


def test_pasted_alert_keeps_its_incident_head_within_the_prompt_budget() -> None:
    llm = _IncidentLLM()

    result = _run(f"{_PASTED_ALERT} {'x' * 2_000}", llm, [])

    user_message = str(llm.seen_messages[0][0]["content"])
    assert _PASTED_ALERT in user_message
    assert "x" * 600 not in user_message
    assert "No evidence tools are connected" in result.primary_response_text


def test_cancelled_incident_turn_stops_before_model_or_tool_work() -> None:
    llm = _IncidentLLM()

    result = _run(
        _INCIDENT_PROMPT,
        llm,
        [_tool("query_logs", {"error": "must not run"})],
        cancelled=True,
    )

    assert result.cancelled is True
    assert result.primary_response_text == ""
    assert llm.invocations == 0
