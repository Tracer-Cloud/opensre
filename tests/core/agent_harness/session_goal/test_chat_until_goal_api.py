"""AgentSession.chat_until_goal delegates the SessionGoal loop to its agent."""

from __future__ import annotations

from typing import Any

import pytest

from core.agent_harness.harness import AgentSession, SessionConfig
from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.session_goal.evaluate import evaluate_session_goal
from core.agent_harness.session_goal.goal import (
    SessionGoal,
    SessionGoalStatus,
    attach_session_goal,
)
from core.agent_harness.session_goal.judge import SessionGoalJudgeVerdict
from core.agent_harness.session_goal.run_until import (
    SessionGoalRunResult,
    run_until_session_goal,
)
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult


def _turn(body: str, *, success: int = 1) -> TurnResult:
    return TurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=success,
            executed_count=success,
            executed_success_count=success,
            has_unhandled_clause=False,
            handled=True,
        ),
        assistant_response_text=body,
    )


def _keep_ticks(**kw: object) -> frozenset[int]:
    newly = kw.get("newly")
    return newly if isinstance(newly, frozenset) else frozenset()


def _evaluate_until_checklist(goal: SessionGoal, result: object, *, session: object = None) -> str:
    return evaluate_session_goal(
        goal,
        result,
        session=session,
        judge=lambda **_kw: SessionGoalJudgeVerdict(
            verdict="NOT_REACHED", reason="checklist still open"
        ),
        validate=_keep_ticks,
    ).status


class _FakeGoalAgent:
    """Stand-in for HeadlessAgent: one dispatch verb, one goal loop over it.

    Records the ``binding`` it was handed so a test can assert AgentSession does
    not push a per-turn binding (which would wipe the agent's bound turn context).
    """

    def __init__(self, session: SessionCore) -> None:
        self._session = session
        self.calls: list[str] = []
        self.binding_seen: Any = "unset"

    def dispatch(self, message: str) -> TurnResult:
        self.calls.append(message)
        stored = self._session.session_goal
        if isinstance(stored, SessionGoal) and stored.checklist:
            nxt = len(stored.completed)
            if nxt < len(stored.checklist):
                attach_session_goal(
                    self._session,
                    stored.with_completed(stored.completed | {nxt}),
                )
        return _turn("working")

    def run_goal(self, text: str, binding: Any = None, **kwargs: Any) -> SessionGoalRunResult:
        self.binding_seen = binding
        return run_until_session_goal(self.dispatch, self._session, text, **kwargs)


class _DispatchOnlyAgent:
    def dispatch(self, _message: str) -> TurnResult:
        return _turn("hi")


def test_chat_until_goal_delegates_to_the_agent_loop_until_achieved() -> None:
    # Arrange: an AgentSession bound to a goal-driving agent.
    session = SessionCore()
    agent = _FakeGoalAgent(session)
    api = AgentSession(SessionConfig(load_env=False))
    api._bound_session = session
    api.attach_agent(agent)  # type: ignore[arg-type]

    # Act: run the goal loop through the public verb.
    outcome = api.chat_until_goal(
        "go",
        goal=SessionGoal(condition="two-step", max_outer_turns=3, checklist=("one", "two")),
        evaluate=_evaluate_until_checklist,
    )

    # Assert: the agent's own loop drove both turns to completion.
    assert len(agent.calls) == 2
    assert outcome.goal.status == SessionGoalStatus.ACHIEVED
    assert "session_goal:" not in (outcome.last_result.assistant_response_text or "")


def test_chat_until_goal_reuses_agent_context_without_a_per_turn_binding() -> None:
    # Arrange: a configured-once agent whose bound tty/tool-hooks must survive.
    session = SessionCore()
    agent = _FakeGoalAgent(session)
    api = AgentSession(SessionConfig(load_env=False))
    api._bound_session = session
    api.attach_agent(agent)  # type: ignore[arg-type]

    # Act.
    api.chat_until_goal("go", goal=SessionGoal(condition="x", max_outer_turns=1))

    # Assert: no binding was pushed, so run_goal reuses the agent's bound context
    # (a sparse TurnBinding would wipe tool_hooks/is_tty by design).
    assert agent.binding_seen is None


def test_chat_until_goal_requires_a_goal_driving_agent() -> None:
    # Arrange: a dispatch-only agent cannot drive the goal loop.
    api = AgentSession(SessionConfig(load_env=False))
    api._bound_session = SessionCore()
    api.attach_agent(_DispatchOnlyAgent())  # type: ignore[arg-type]

    # Act / Assert: chat_until_goal refuses it rather than silently single-turning.
    with pytest.raises(RuntimeError, match="drives the session-goal loop"):
        api.chat_until_goal("go", goal=SessionGoal(condition="x", max_outer_turns=1))
