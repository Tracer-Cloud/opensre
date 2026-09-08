"""Newly ticked checklist items are checked before they can close a /goal."""

from __future__ import annotations

from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.session_goal.evaluate import evaluate_session_goal
from core.agent_harness.session_goal.goal import (
    SessionGoal,
    SessionGoalReason,
    SessionGoalStatus,
    attach_session_goal,
)
from core.agent_harness.session_goal.judge import SessionGoalJudgeVerdict
from core.agent_harness.session_goal.validate import (
    ChecklistItemVerdict,
    ChecklistTickVerdict,
    invoke_checklist_tick_validator,
    kept_tick_indices,
    rejected_tick_reasons,
)
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from core.llm.types import AgentLLMResponse


def _result(text: str, *, success: int = 0) -> TurnResult:
    return TurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=success,
            executed_count=success,
            executed_success_count=success,
            has_unhandled_clause=False,
            handled=True,
        ),
        assistant_response_text=text,
    )


def _not_yet(**_kw: object) -> SessionGoalJudgeVerdict:
    return SessionGoalJudgeVerdict(verdict="NOT_REACHED", reason="not yet")


def test_kept_tick_indices_keep_all_when_the_validator_is_unavailable() -> None:
    newly = frozenset({0, 1})
    assert kept_tick_indices(None, newly=newly) == newly


def test_kept_tick_indices_drop_invalid_items() -> None:
    parsed = ChecklistTickVerdict(
        items=[
            ChecklistItemVerdict(index=0, verdict="VALID", reason="tool listed it"),
            ChecklistItemVerdict(index=1, verdict="INVALID", reason="no supporting work"),
        ]
    )
    assert kept_tick_indices(parsed, newly=frozenset({0, 1})) == frozenset({0})


def test_kept_tick_indices_drop_a_tick_the_validator_did_not_mention() -> None:
    # Arrange: the validator answered for item 0 only.
    parsed = ChecklistTickVerdict(
        items=[ChecklistItemVerdict(index=0, verdict="VALID", reason="ok")]
    )

    # Act / Assert: an unmentioned tick is unconfirmed, and the status line says so.
    assert kept_tick_indices(parsed, newly=frozenset({0, 1})) == frozenset({0})
    assert rejected_tick_reasons(parsed, newly=frozenset({0, 1})) == (
        "validator did not confirm item 1",
    )


def test_validator_rejects_a_tick_so_the_goal_stays_open() -> None:
    session = SessionCore()
    goal = SessionGoal(condition="two checks", checklist=("A", "B"))
    attach_session_goal(session, goal)
    attach_session_goal(session, goal.with_completed(frozenset({0})))

    verdict = evaluate_session_goal(
        goal,
        _result("claimed A.", success=1),
        session=session,
        judge=_not_yet,
        validate=lambda **_kw: frozenset(),
    )

    assert verdict.status == SessionGoalStatus.ACTIVE
    assert session.session_goal is not None
    assert session.session_goal.completed == frozenset()


def test_validator_unavailable_keeps_ticks_but_does_not_auto_complete() -> None:
    session = SessionCore()
    goal = SessionGoal(condition="one check", checklist=("A",))
    attach_session_goal(session, goal)
    attach_session_goal(session, goal.with_completed(frozenset({0})))

    verdict = evaluate_session_goal(
        goal,
        _result("done", success=1),
        session=session,
        judge=_not_yet,
        validate=lambda **_kw: None,
    )

    assert verdict.status == SessionGoalStatus.ACTIVE
    assert verdict.reason != SessionGoalReason.CHECKLIST_COMPLETE
    assert session.session_goal is not None
    assert session.session_goal.completed == frozenset({0})


def test_invoke_validator_returns_none_on_transport_failure() -> None:
    class _Boom:
        model_id = "test"

        def invoke(self, messages, *, system=None, tools=None):  # noqa: ANN001
            _ = (messages, system, tools)
            raise RuntimeError("classifier down")

        def with_structured_output(self, model):  # noqa: ANN001
            _ = model
            raise RuntimeError("classifier down")

    parsed = invoke_checklist_tick_validator(
        _Boom(),  # type: ignore[arg-type]
        condition="two checks",
        reply="ticked A",
        evidence=True,
        ticked=((0, "A"),),
    )
    assert parsed is None


def test_invoke_validator_fails_closed_on_free_text() -> None:
    class _LLM:
        model_id = "test"

        def invoke(self, messages, *, system=None, tools=None):  # noqa: ANN001
            _ = (messages, system, tools)
            return AgentLLMResponse(content="VALID — looks fine")

        def tool_schemas(self, tools):  # noqa: ANN001
            _ = tools
            return []

    parsed = invoke_checklist_tick_validator(
        _LLM(),  # type: ignore[arg-type]
        condition="two checks",
        reply="ticked A",
        evidence=True,
        ticked=((0, "A"),),
    )
    assert parsed is None


def test_rejected_tick_reasons_name_only_the_refused_items_in_index_order() -> None:
    # Arrange
    parsed = ChecklistTickVerdict(
        items=[
            ChecklistItemVerdict(index=2, verdict="INVALID", reason="no output for C"),
            ChecklistItemVerdict(index=0, verdict="VALID", reason="ok"),
            ChecklistItemVerdict(index=1, verdict="INVALID", reason=""),
        ]
    )

    # Act
    reasons = rejected_tick_reasons(parsed, newly=frozenset({0, 1, 2}))

    # Assert: a blank reason still says which item, and the order follows the checklist.
    assert reasons == ("item 1 not supported by the reply", "no output for C")
    assert rejected_tick_reasons(None, newly=frozenset({0})) == ()
