"""``run_turn`` is the root observation of one trace.

Langfuse derives the trace's input/output and session grouping from this
root, so the user text, the reply and the session id must land here — not on
the agent loop underneath, which may run several times per turn.
"""

from __future__ import annotations

from typing import Any, cast

from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.turns.orchestrator import ExecuteActions, run_turn
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from infrastructure.observability.trace.observations import (
    ObservationKind,
    observe_agent,
    set_observation_sink,
)
from tests.utils.observations import RecordingObservationSink


class _Accounting:
    def record_action_result(self, _result: ToolCallingTurnResult) -> None:
        return None

    def finalize(self, result: TurnResult) -> TurnResult:
        return result


def test_run_turn_opens_root_span_with_session_and_reply() -> None:
    sink = RecordingObservationSink()
    set_observation_sink(sink)
    session = SessionCore(store=InMemorySessionStore())

    def execute_actions(_text: str, **_kwargs: Any) -> ToolCallingTurnResult:
        with observe_agent("run-react-loop"):
            pass
        return ToolCallingTurnResult(1, 1, 1, False, True, response_text="42")

    result = run_turn(
        "meaning of life?",
        session,
        execute_actions=cast(ExecuteActions, execute_actions),
        accounting=_Accounting(),
        surface="gateway",
    )

    assert result.primary_response_text == "42"
    (root,) = sink.of_kind(ObservationKind.SPAN)
    assert root.name == "handle-turn"
    assert root.parent is None
    assert root.input == "meaning of life?"
    assert root.output == "42"
    assert root.trace is not None
    assert root.trace.session_id == session.session_id
    assert root.trace.tags == ("gateway",)
    assert root.metadata["executed_success_count"] == 1
    (agent,) = sink.of_kind(ObservationKind.AGENT)
    assert agent.parent is root
