"""``run_turn`` is the root observation of one trace.

Langfuse derives the trace's input/output, session grouping and user from this
root, so the user text, the reply, the session id and the actor must land here
— not on the agent loop underneath, which may run several times per turn. The
outermost turn owns the session id: nested and scheduled turns inherit it.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.turns import orchestrator
from core.agent_harness.turns.orchestrator import ExecuteActions, run_turn
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from infrastructure.observability.trace.observations import (
    ObservationKind,
    observe_agent,
    set_observation_sink,
)
from infrastructure.observability.trace.trace_session import inherit_trace_session
from infrastructure.observability.trace.user_identity import TraceIdentity
from tests.utils.observations import RecordedObservation, RecordingObservationSink


class _Accounting:
    def record_action_result(self, _result: ToolCallingTurnResult) -> None:
        return None

    def finalize(self, result: TurnResult) -> TurnResult:
        return result


def _reply(text: str) -> ToolCallingTurnResult:
    return ToolCallingTurnResult(1, 1, 1, False, True, response_text=text)


def _turn(text: str, session: SessionCore, execute_actions: Any) -> TurnResult:
    return run_turn(
        text,
        session,
        execute_actions=cast(ExecuteActions, execute_actions),
        accounting=_Accounting(),
        surface="gateway",
    )


def _roots(sink: RecordingObservationSink) -> list[RecordedObservation]:
    return [record for record in sink.of_kind(ObservationKind.SPAN) if record.name == "handle-turn"]


@pytest.fixture
def sink() -> RecordingObservationSink:
    recording = RecordingObservationSink()
    set_observation_sink(recording)
    return recording


@pytest.fixture
def local_identity(monkeypatch: pytest.MonkeyPatch) -> TraceIdentity:
    identity = TraceIdentity(user_id="user_clerk_1", installation_id="install-1")
    monkeypatch.setattr(orchestrator, "resolve_trace_identity", lambda: identity)
    return identity


def test_run_turn_opens_root_span_with_session_actor_and_reply(
    sink: RecordingObservationSink,
) -> None:
    session = SessionCore(store=InMemorySessionStore())

    def execute_actions(_text: str, **_kwargs: Any) -> ToolCallingTurnResult:
        with observe_agent("run-react-loop"):
            pass
        return _reply("42")

    scope = StorageScope(principal=Principal.org("org_1"), actor=Actor("U_ALICE"))
    with bound_storage_scope(scope):
        result = _turn("meaning of life?", session, execute_actions)

    assert result.primary_response_text == "42"
    (root,) = _roots(sink)
    assert root.parent is None
    assert root.input == "meaning of life?"
    assert root.output == "42"
    assert root.trace is not None
    assert root.trace.session_id == session.session_id
    assert root.trace.user_id == "U_ALICE"
    assert root.trace.tags == ("gateway",)
    assert "installation_id" not in (root.trace.metadata or {})
    assert root.metadata["executed_success_count"] == 1
    (agent,) = sink.of_kind(ObservationKind.AGENT)
    assert agent.parent is root


def test_local_turn_carries_account_user_and_installation(
    sink: RecordingObservationSink, local_identity: TraceIdentity
) -> None:
    session = SessionCore(store=InMemorySessionStore())

    _turn("hi", session, lambda _text, **_kw: _reply("hello"))

    (root,) = _roots(sink)
    assert root.trace is not None
    assert root.trace.user_id == local_identity.user_id
    assert root.trace.metadata == {"surface": "gateway", "installation_id": "install-1"}


def test_nested_turn_inherits_the_outer_session(
    sink: RecordingObservationSink, local_identity: TraceIdentity
) -> None:
    """A tool or loop that drives a headless turn must not open a second session."""
    outer = SessionCore(store=InMemorySessionStore())
    throwaway = SessionCore(store=InMemorySessionStore())

    def execute_actions(_text: str, **_kwargs: Any) -> ToolCallingTurnResult:
        _turn("tick", throwaway, lambda _t, **_kw: _reply("inner"))
        return _reply("outer")

    _turn("run the loop", outer, execute_actions)

    outer_root, inner_root = _roots(sink)
    assert inner_root.parent is outer_root
    assert outer_root.trace is not None and inner_root.trace is not None
    assert inner_root.trace.session_id == outer.session_id
    assert inner_root.trace.session_id != throwaway.session_id


def test_pre_bound_trace_session_wins_over_the_throwaway_session(
    sink: RecordingObservationSink, local_identity: TraceIdentity
) -> None:
    """Scheduler ticks bind the hosting session on their worker thread before the turn."""
    throwaway = SessionCore(store=InMemorySessionStore())

    with inherit_trace_session(
        "shell-session", tags=("scheduled",), metadata={"task_id": "cf9d8a4169ac"}
    ):
        _turn("tick", throwaway, lambda _t, **_kw: _reply("no repair needed"))

    (root,) = _roots(sink)
    assert root.trace is not None
    assert root.trace.session_id == "shell-session"
    assert root.trace.tags == ("gateway", "scheduled")
    assert root.trace.metadata == {
        "surface": "gateway",
        "task_id": "cf9d8a4169ac",
        "installation_id": "install-1",
    }
