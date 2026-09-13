"""Tool execution records one observation per call under the caller's context."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolRole
from core.llm.types import ToolCall
from core.tool.contracts import AgentTool
from core.tool.execution import execute_tool_calls
from infrastructure.observability.trace.observations import (
    ObservationKind,
    ObservationLevel,
    observe_agent,
    set_observation_sink,
)
from tests.utils.observations import RecordingObservationSink

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}


def _tool(name: str, execute: Any, *, role: ToolRole = ToolRole.ACTION) -> AgentTool:
    return AgentTool(
        name=name,
        description="test tool",
        input_schema=_SCHEMA,
        execute=execute,
        role=role,
        source="agent",
    )


def test_tool_observations_are_children_of_the_calling_observation() -> None:
    sink = RecordingObservationSink()
    set_observation_sink(sink)

    def _echo(args: dict[str, Any], _ctx: Any) -> dict[str, Any]:
        return {"value": args["value"]}

    def _boom(_args: dict[str, Any], _ctx: Any) -> dict[str, Any]:
        raise RuntimeError("tool exploded")

    # One action plus one bookkeeping call is the largest batch the runtime runs.
    with observe_agent("run-react-loop") as parent:
        results = execute_tool_calls(
            [
                ToolCall(id="a-1", name="alpha", input={"value": "1"}),
                ToolCall(id="b-1", name="beta", input={"value": "2"}),
            ],
            [_tool("alpha", _echo, role=ToolRole.BOOKKEEPING), _tool("beta", _boom)],
            {},
        )

    assert [r.is_error for r in results] == [False, True]
    tools = sink.of_kind(ObservationKind.TOOL)
    assert [t.name for t in tools] == ["alpha", "beta"]
    assert all(t.parent is parent for t in tools)

    by_name = {t.name: t for t in tools}
    assert by_name["alpha"].input == {"value": "1"}
    assert by_name["alpha"].output == {"value": "1"}
    assert by_name["alpha"].metadata["tool_call_id"] == "a-1"
    assert by_name["alpha"].level is None
    assert by_name["beta"].level is ObservationLevel.ERROR
    assert by_name["beta"].metadata["is_error"] is True
