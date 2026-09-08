"""SessionGoal evaluate: evidence gate, checklist ticks, pending choice."""

from __future__ import annotations

from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.session_goal.evaluate import (
    build_session_goal_evaluator,
    evaluate_session_goal,
    turn_has_session_goal_evidence,
)
from core.agent_harness.session_goal.goal import (
    SessionGoal,
    SessionGoalReason,
    SessionGoalStatus,
    attach_session_goal,
)
from core.agent_harness.session_goal.judge import SessionGoalJudgeVerdict
from core.agent_harness.session_goal.run_until import run_until_session_goal
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from core.llm.types import AgentLLMResponse


def _result(
    text: str,
    *,
    executed: int = 0,
    success: int = 0,
) -> TurnResult:
    return TurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=executed,
            executed_count=executed,
            executed_success_count=success,
            has_unhandled_clause=False,
            handled=True,
        ),
        assistant_response_text=text,
    )


def _reached(**_kw: object) -> SessionGoalJudgeVerdict:
    return SessionGoalJudgeVerdict(verdict="GOAL_REACHED", reason="the reply answers it")


def _not_yet(**_kw: object) -> SessionGoalJudgeVerdict:
    return SessionGoalJudgeVerdict(verdict="NOT_REACHED", reason="not yet")


def _keep_ticks(**kw: object) -> frozenset[int]:
    newly = kw.get("newly")
    return newly if isinstance(newly, frozenset) else frozenset()


def test_turn_has_session_goal_evidence_requires_a_tool_that_succeeded() -> None:
    """Evidence is work that worked, not work that was attempted."""
    assert turn_has_session_goal_evidence(_result("done")) is False
    assert turn_has_session_goal_evidence(_result("done", executed=1)) is False
    assert turn_has_session_goal_evidence(_result("done", executed=1, success=1)) is True


def test_slash_capture_waiting_reason_does_not_achieve_host_goal() -> None:
    """Regression: /goal set turn captured status text and falsely achieved."""
    from core.agent_harness.session_goal.progress import (
        SESSION_GOAL_PROGRESS_MARK,
        SESSION_GOAL_USER_WORD,
    )

    session = SessionCore()
    goal = SessionGoal(
        condition="How many Windows users?",
        max_outer_turns=4,
        host_owned=True,
    )
    attach_session_goal(session, goal)
    progress_text = (
        f"{SESSION_GOAL_PROGRESS_MARK} {SESSION_GOAL_USER_WORD} active · 0s · turn 0/4 · +0 tokens\n"
        "  condition: How many Windows users?\n"
        f"  reason: {SessionGoalReason.WAITING_HOST_SIGNAL}"
    )
    verdict = evaluate_session_goal(
        goal,
        _result(progress_text, executed=1, success=1),
        session=session,
        judge=_reached,
    )
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert session.session_goal is not None
    assert session.session_goal.status == SessionGoalStatus.ACTIVE


def test_goal_set_attach_turn_does_not_consume_outer_budget_on_shell() -> None:
    """Shell ``/goal set`` attach + autosubmit: first work turn is the next chat."""
    session = SessionCore()
    session.terminal = type("T", (), {})()
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        if message.startswith("/goal"):
            attach_session_goal(
                session,
                SessionGoal(
                    condition="count windows users",
                    max_outer_turns=4,
                    host_owned=True,
                ),
            )
            return _result(
                f"◎ /goal active\n  reason: {SessionGoalReason.WAITING_HOST_SIGNAL}",
                executed=1,
                success=1,
            )
        return _result("284 users.", executed=1, success=1)

    outcome = run_until_session_goal(
        _chat,
        session,
        "/goal set count windows users",
        evaluate=lambda goal, result, *, session=None: (
            evaluate_session_goal(goal, result, session=session, judge=_reached).status
        ),
    )
    assert len(turns) == 1
    assert outcome.turn_count == 0
    assert outcome.goal.status == SessionGoalStatus.ACTIVE
    assert outcome.goal.turns_used == 0

    outcome2 = run_until_session_goal(
        _chat,
        session,
        "count windows users",
        evaluate=lambda goal, result, *, session=None: (
            evaluate_session_goal(goal, result, session=session, judge=_reached).status
        ),
    )
    assert outcome2.goal.status == SessionGoalStatus.ACHIEVED
    assert outcome2.goal.turns_used == 1


def test_goal_set_on_headless_starts_the_condition_turn() -> None:
    """Slack/Telegram have no REPL autosubmit — start the condition in this loop."""
    session = SessionCore()
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        if message.startswith("/goal"):
            attach_session_goal(
                session,
                SessionGoal(
                    condition="count windows users",
                    max_outer_turns=4,
                    host_owned=True,
                ),
            )
            return _result(
                f"◎ /goal active\n  reason: {SessionGoalReason.WAITING_HOST_SIGNAL}",
                executed=1,
                success=1,
            )
        return _result("284 users.", executed=1, success=1)

    outcome = run_until_session_goal(
        _chat,
        session,
        "/goal set count windows users",
        evaluate=lambda goal, result, *, session=None: (
            evaluate_session_goal(goal, result, session=session, judge=_reached).status
        ),
    )
    assert turns == ["/goal set count windows users", "count windows users"]
    assert outcome.goal.status == SessionGoalStatus.ACHIEVED
    assert outcome.goal.turns_used == 1


def test_host_owned_fallback_route_without_tool_evidence_stays_active() -> None:
    session = SessionCore()
    attach_session_goal(
        session,
        SessionGoal(
            condition="How many Windows users in the last 7 days?",
            max_outer_turns=4,
            host_owned=True,
        ),
    )
    verdict = evaluate_session_goal(
        session.session_goal,  # type: ignore[arg-type]
        TurnResult(
            final_intent="cli_agent_fallback",
            action_result=ToolCallingTurnResult(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
                has_unhandled_clause=False,
                handled=False,
            ),
            assistant_response_text=(
                "I could not get a live count. Here is draft HogQL and a setup CTA."
            ),
        ),
        session=session,
        judge=_reached,
    )
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert SessionGoalReason.NEED_TOOL_EVIDENCE in verdict.reason


def test_checklist_complete_with_tool_ticks_achieves_without_a_judge() -> None:
    session = SessionCore()
    goal = SessionGoal(
        condition="checklist",
        checklist=("A", "B"),
        completed=frozenset({0}),
    )
    attach_session_goal(session, goal)
    attach_session_goal(session, goal.with_completed(frozenset({0, 1})))
    verdict = evaluate_session_goal(
        goal,
        _result("Finished B.", executed=1, success=1),
        session=session,
        judge=_not_yet,
        validate=_keep_ticks,
    )
    assert verdict.status == SessionGoalStatus.ACHIEVED
    assert verdict.reason == SessionGoalReason.CHECKLIST_COMPLETE


def test_done_tags_in_the_reply_do_not_tick_or_complete() -> None:
    session = SessionCore()
    goal = SessionGoal(condition="checklist", checklist=("A", "B"))
    attach_session_goal(session, goal)
    verdict = evaluate_session_goal(
        goal,
        _result("Finished. session_goal:done=0,1", executed=1, success=1),
        session=session,
        judge=_not_yet,
        validate=_keep_ticks,
    )
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert session.session_goal is not None
    assert session.session_goal.completed == frozenset()


def test_stored_findings_count_as_evidence_for_a_reached_verdict() -> None:
    verdict = evaluate_session_goal(
        SessionGoal(
            condition="How many Windows users?",
            findings=("272 Windows users in the last 7 days",),
        ),
        _result("272 Windows users."),
        judge=_reached,
        validate=_keep_ticks,
    )
    assert verdict.status == SessionGoalStatus.ACHIEVED


def test_outer_loop_rejects_bare_claim_until_budget() -> None:
    session = SessionCore()
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        return _result("pretending. session_goal:achieved")

    outcome = run_until_session_goal(
        _chat,
        session,
        "go",
        goal=SessionGoal(condition="real work", max_outer_turns=2),
        evaluate=lambda goal, result, *, session=None: (
            evaluate_session_goal(goal, result, session=session, judge=_reached).status
        ),
    )

    assert len(turns) == 2
    assert outcome.goal.status == SessionGoalStatus.BUDGET_EXHAUSTED


def test_llm_evaluator_rejects_soft_achieve() -> None:
    class _LLM:
        model_id = "test"

        def invoke(self, messages, *, system=None, tools=None):  # noqa: ANN001
            _ = (messages, system, tools)
            return AgentLLMResponse(
                content='{"verdict": "NOT_REACHED", "reason": "still missing the SHA filter"}'
            )

        def tool_schemas(self, tools):  # noqa: ANN001
            _ = tools
            return []

    evaluate = build_session_goal_evaluator(lambda: _LLM())  # type: ignore[arg-type]
    session = SessionCore()
    goal = SessionGoal(condition="finish migration", max_outer_turns=3)
    attach_session_goal(session, goal)

    status = evaluate(
        goal,
        _result("looks done", executed=1, success=1),
        session=session,
    )
    assert status == SessionGoalStatus.ACTIVE
    assert session.session_goal is not None
    assert session.session_goal.status == SessionGoalStatus.ACTIVE
    assert "SHA" in session.session_goal.last_reason


def test_llm_reject_survives_outer_loop_session_reread() -> None:
    class _LLM:
        model_id = "test"

        def invoke(self, messages, *, system=None, tools=None):  # noqa: ANN001
            _ = (messages, system, tools)
            return AgentLLMResponse(content='{"verdict": "NOT_REACHED", "reason": "not reached"}')

        def tool_schemas(self, tools):  # noqa: ANN001
            _ = tools
            return []

    session = SessionCore()
    progress_updates: list[str] = []

    def _chat(message: str) -> TurnResult:
        _ = message
        return _result("Patched.", executed=1, success=1)

    outcome = run_until_session_goal(
        _chat,
        session,
        "go",
        goal=SessionGoal(condition="finish migration", max_outer_turns=2),
        evaluate=build_session_goal_evaluator(lambda: _LLM()),  # type: ignore[arg-type]
        on_progress=lambda g: progress_updates.append(g.status),
    )

    assert outcome.goal.status == SessionGoalStatus.BUDGET_EXHAUSTED
    assert SessionGoalStatus.ACHIEVED not in progress_updates
    assert session.session_goal is not None
    assert session.session_goal.status == SessionGoalStatus.BUDGET_EXHAUSTED


def test_budget_exhaustion_reports_progress_once() -> None:
    session = SessionCore()
    progress_updates: list[str] = []

    def _chat(message: str) -> TurnResult:
        _ = message
        return _result("still working")

    outcome = run_until_session_goal(
        _chat,
        session,
        "go",
        goal=SessionGoal(condition="never done", max_outer_turns=1, host_owned=True),
        evaluate=lambda goal, result, *, session=None: (
            evaluate_session_goal(goal, result, session=session, judge=_not_yet).status
        ),
        on_progress=lambda g: progress_updates.append(f"{g.status}:{g.last_reason}"),
    )

    assert outcome.goal.status == SessionGoalStatus.BUDGET_EXHAUSTED
    budget_updates = [
        p for p in progress_updates if p.startswith(SessionGoalStatus.BUDGET_EXHAUSTED)
    ]
    assert len(budget_updates) == 1


def test_llm_evaluator_confirms_soft_achieve() -> None:
    class _LLM:
        model_id = "test"

        def invoke(self, messages, *, system=None, tools=None):  # noqa: ANN001
            _ = (messages, system, tools)
            return AgentLLMResponse(
                content='{"verdict": "GOAL_REACHED", "reason": "tools returned the answer"}'
            )

        def tool_schemas(self, tools):  # noqa: ANN001
            _ = tools
            return []

    evaluate = build_session_goal_evaluator(lambda: _LLM())  # type: ignore[arg-type]
    status = evaluate(
        SessionGoal(condition="finish migration", max_outer_turns=3),
        _result("patched", executed=1, success=1),
    )
    assert status == SessionGoalStatus.ACHIEVED


def test_llm_evaluator_fails_closed_on_free_text_verdict() -> None:
    class _LLM:
        model_id = "test"

        def invoke(self, messages, *, system=None, tools=None):  # noqa: ANN001
            _ = (messages, system, tools)
            return AgentLLMResponse(content="GOAL_REACHED — looks done to me")

        def tool_schemas(self, tools):  # noqa: ANN001
            _ = tools
            return []

    session = SessionCore()
    goal = SessionGoal(condition="finish migration", max_outer_turns=3)
    attach_session_goal(session, goal)
    status = build_session_goal_evaluator(lambda: _LLM())(  # type: ignore[arg-type]
        goal,
        _result("patched", executed=1, success=1),
        session=session,
    )
    assert status == SessionGoalStatus.ACTIVE
    assert session.session_goal is not None
    assert session.session_goal.status == SessionGoalStatus.ACTIVE


def test_pending_user_choice_outranks_a_reached_verdict_with_evidence() -> None:
    from core.agent_harness.session.pending_choice import PendingUserChoice

    session = SessionCore()
    session.pending_user_choice = PendingUserChoice(
        title="Which environment?", options=("staging", "production")
    )
    goal = SessionGoal(
        condition="restart the service",
        checklist=("Pick the environment",),
        completed=frozenset({0}),
    )
    attach_session_goal(session, goal)

    verdict = evaluate_session_goal(
        goal,
        _result("Restarted.", executed=1, success=1),
        session=session,
        judge=_reached,
    )

    assert verdict.status == SessionGoalStatus.ACTIVE
    assert verdict.reason == SessionGoalReason.WAITING_USER_CHOICE


def test_evidence_is_false_when_the_success_counts_are_not_numbers() -> None:
    class _BadCounts:
        executed_success_count = "two"

    class _BadResult:
        action_result = _BadCounts()
        assistant_response_text = "done"

    assert turn_has_session_goal_evidence(_BadResult()) is False


def test_a_met_verdict_ticks_every_checklist_item() -> None:
    # Arrange: a two-item checklist with nothing ticked yet.
    session = SessionCore()
    goal = SessionGoal(condition="two checks", checklist=("A", "B"))
    attach_session_goal(session, goal)

    # Act: the judge says met after successful tool work.
    verdict = evaluate_session_goal(
        goal,
        _result("Both done.", executed=1, success=1),
        session=session,
        judge=_reached,
    )

    # Assert: the stored goal shows [x] on every item, not an achieved goal with open boxes.
    assert verdict.status == SessionGoalStatus.ACHIEVED
    assert session.session_goal is not None
    assert session.session_goal.completed == frozenset({0, 1})


def test_without_a_judge_only_a_ticked_checklist_can_close_the_goal() -> None:
    # Arrange: no judge, no judge client — an in-memory host.
    session = SessionCore()
    open_goal = SessionGoal(condition="count users")
    attach_session_goal(session, open_goal)

    # Act
    verdict = evaluate_session_goal(
        open_goal, _result("284 users.", executed=1, success=1), session=session
    )

    # Assert: a confident tool-backed reply is not enough without a judge.
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert verdict.reason == SessionGoalReason.JUDGE_UNAVAILABLE


def test_a_rejected_tick_names_its_reason_on_the_status_line() -> None:
    # Arrange: the model ticked A; the validator refuses it.
    from core.agent_harness.session_goal.validate import (
        ChecklistItemVerdict,
        ChecklistTickVerdict,
    )

    class _Validator:
        model_id = "test"

        def invoke(self, messages, *, system=None, tools=None):  # noqa: ANN001
            _ = (messages, system, tools)
            return AgentLLMResponse(
                content=ChecklistTickVerdict(
                    items=[
                        ChecklistItemVerdict(
                            index=0, verdict="INVALID", reason="no run was listed for A"
                        )
                    ]
                ).model_dump_json()
            )

        def tool_schemas(self, tools):  # noqa: ANN001
            _ = tools
            return []

    session = SessionCore()
    goal = SessionGoal(condition="two checks", checklist=("A", "B"))
    attach_session_goal(session, goal)
    attach_session_goal(session, goal.with_completed(frozenset({0})))

    # Act
    verdict = evaluate_session_goal(
        goal,
        _result("Did A.", executed=1, success=1),
        session=session,
        judge=_not_yet,
        validate_llm=_Validator(),  # type: ignore[arg-type]
    )

    # Assert: the tick is gone and the user can see why.
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert "tick rejected: no run was listed for A" in verdict.reason
    assert session.session_goal is not None
    assert session.session_goal.completed == frozenset()


def test_a_judge_client_that_cannot_be_built_keeps_the_goal_active() -> None:
    # Arrange: the host's factory raises (no credentials).
    def _broken_factory() -> object:
        raise RuntimeError("no llm configured")

    evaluate = build_session_goal_evaluator(_broken_factory)  # type: ignore[arg-type]
    session = SessionCore()
    goal = SessionGoal(condition="count users")
    attach_session_goal(session, goal)

    # Act
    status = evaluate(goal, _result("284 users.", executed=1, success=1), session=session)

    # Assert
    assert status == SessionGoalStatus.ACTIVE
    assert session.session_goal is not None
    assert session.session_goal.last_reason == SessionGoalReason.JUDGE_UNAVAILABLE


def test_the_tick_tool_itself_is_not_evidence_for_a_met_verdict() -> None:
    # Arrange: the only successful tool this turn was session_goal_complete.
    from core.agent_harness.tools import ActionToolScope
    from tools.interactive_shell.actions.session_goal import execute_session_goal_complete_tool

    session = SessionCore()
    goal = SessionGoal(condition="two checks", checklist=("A", "B"))
    attach_session_goal(session, goal)
    execute_session_goal_complete_tool(
        {"items": [0, 1]}, ActionToolScope(session=session, console=object())
    )

    # Act: the judge says met; the turn counts one success (the tick call).
    verdict = evaluate_session_goal(
        goal,
        _result("Both done.", executed=1, success=1),
        session=session,
        judge=_reached,
    )

    # Assert: a tick cannot vouch for itself, so the goal stays open.
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert SessionGoalReason.NEED_TOOL_EVIDENCE in verdict.reason
    assert session.session_goal is not None
    assert session.session_goal.bookkeeping_calls == 0
