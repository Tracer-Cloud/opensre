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
    assert turns[0] == "/goal set count windows users"
    assert "count windows users" in turns[1]
    assert "[session_goal]" in turns[1]
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


def test_unfinished_checklist_keeps_a_not_reached_verdict_active() -> None:
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
    goal = SessionGoal(
        condition="finish migration",
        max_outer_turns=3,
        checklist=("list the runs", "filter by SHA"),
    )
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


def test_not_yet_keeps_the_goal_open_even_after_successful_tools() -> None:
    """Claude-like: the judge's not-yet starts another turn. Tools are not enough."""
    session = SessionCore()
    goal = SessionGoal(condition="count Windows users", max_outer_turns=3)
    attach_session_goal(session, goal)
    verdict = evaluate_session_goal(
        goal,
        _result("No evidence found.", executed=1, success=1),
        session=session,
        judge=_not_yet,
    )
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert verdict.reason == "not yet"
    assert session.session_goal is not None
    assert session.session_goal.verdict_repeated is False


def test_repeats_previous_is_ignored_when_there_is_no_previous_reason() -> None:
    session = SessionCore()
    goal = SessionGoal(condition="write ok to a file")
    attach_session_goal(session, goal)
    verdict = evaluate_session_goal(
        goal,
        _result("I will write it next turn."),
        session=session,
        judge=lambda **_kw: SessionGoalJudgeVerdict(
            verdict="NOT_REACHED",
            reason="need a successful write",
            repeats_previous=True,
        ),
    )
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert session.session_goal is not None
    assert session.session_goal.verdict_repeated is False


def test_not_yet_after_tools_runs_another_turn() -> None:
    """Live miss: six gh calls + 'no evidence found' used to close /goal."""
    session = SessionCore()
    turns: list[str] = []

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        if len(turns) == 1:
            return _result("No evidence found.", executed=6, success=6)
        return _result("Only #6123 Release was re-run to green.", executed=1, success=1)

    seen = {"n": 0}

    def _judge(**_kw: object) -> SessionGoalJudgeVerdict:
        seen["n"] += 1
        if seen["n"] == 1:
            return _not_yet()
        return _reached()

    outcome = run_until_session_goal(
        _chat,
        session,
        "go",
        goal=SessionGoal(condition="which merged PRs were re-run to green", max_outer_turns=4),
        evaluate=lambda goal, result, *, session=None: (
            evaluate_session_goal(goal, result, session=session, judge=_judge).status
        ),
    )

    assert len(turns) == 2
    assert outcome.goal.status == SessionGoalStatus.ACHIEVED


def _failed_deploy_result() -> TurnResult:
    return TurnResult(
        "cli_agent_handled",
        ToolCallingTurnResult(
            1,
            1,
            0,
            False,
            True,
            tool_evidence="Tool: deploy\nArguments: {}\nOutcome: error\nResult: rollout failed",
            evidence_success_count=0,
        ),
        "Deployed.",
    )


def test_a_failed_tool_this_turn_blocks_a_reached_verdict() -> None:
    verdict = evaluate_session_goal(
        SessionGoal(
            condition="deploy prod",
            findings=("listed the target",),
            tool_success_seen=True,
        ),
        _failed_deploy_result(),
        judge=_reached,
    )
    assert verdict.status == SessionGoalStatus.ACTIVE


def test_a_failed_tool_this_turn_blocks_host_accept() -> None:
    verdict = evaluate_session_goal(
        SessionGoal(
            condition="deploy prod",
            findings=("listed the target",),
            tool_success_seen=True,
        ),
        _failed_deploy_result(),
        judge=_not_yet,
    )
    assert verdict.status == SessionGoalStatus.ACTIVE


def test_overflowed_evidence_blocks_a_reached_verdict() -> None:
    verdict = evaluate_session_goal(
        SessionGoal(
            condition="deploy prod",
            findings=("earlier work",),
            tool_evidence=None,
            tool_success_seen=True,
        ),
        _result("Deployed.", executed=1, success=1),
        judge=_reached,
    )
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert verdict.reason == SessionGoalReason.UNVERIFIED_OVERFLOW


def test_a_later_success_does_not_let_the_host_ignore_a_failed_tool() -> None:
    action = ToolCallingTurnResult(
        2,
        2,
        1,
        False,
        True,
        tool_evidence=(
            "Tool: delete_job\nArguments: {}\nOutcome: error\nResult: denied\n\n"
            "Tool: read_status\nArguments: {}\nOutcome: success\nResult: still there"
        ),
        evidence_success_count=1,
    )
    verdict = evaluate_session_goal(
        SessionGoal(condition="remove scheduled jobs"),
        TurnResult("cli_agent_handled", action, "Removed."),
    )
    assert verdict.status == SessionGoalStatus.ACTIVE


def test_a_later_status_success_does_not_recover_a_failed_write() -> None:
    action = ToolCallingTurnResult(
        2,
        2,
        1,
        False,
        True,
        tool_evidence=(
            "Tool: delete_job\nArguments: {}\nOutcome: error\nResult: denied\n\n"
            "Tool: read_status\nArguments: {}\nOutcome: success\nResult: still there"
        ),
        evidence_success_count=1,
    )
    verdict = evaluate_session_goal(
        SessionGoal(condition="remove scheduled jobs"),
        TurnResult("cli_agent_handled", action, "Removed."),
        judge=_reached,
    )
    assert verdict.status == SessionGoalStatus.ACTIVE


def test_a_recovered_failure_does_not_block_a_reached_verdict() -> None:
    action = ToolCallingTurnResult(
        2,
        2,
        1,
        False,
        True,
        tool_evidence=(
            "Tool: list_jobs\nArguments: {}\nOutcome: error\nResult: timeout\n\n"
            "Tool: delete_job\nArguments: {}\nOutcome: success\nResult: removed"
        ),
        evidence_success_count=1,
    )
    verdict = evaluate_session_goal(
        SessionGoal(condition="remove scheduled jobs"),
        TurnResult("cli_agent_handled", action, "Removed the jobs."),
        judge=lambda **_kw: SessionGoalJudgeVerdict(
            verdict="GOAL_REACHED",
            reason="the jobs are gone",
            evidence_quote="Result: removed",
        ),
    )
    assert verdict.status == SessionGoalStatus.ACHIEVED


def test_no_judge_complete_checklist_stays_active_when_a_tool_failed() -> None:
    session = SessionCore()
    goal = SessionGoal(
        condition="checklist",
        checklist=("A", "B"),
        completed=frozenset({0}),
        findings=("listed the jobs",),
        tool_success_seen=True,
    )
    attach_session_goal(session, goal)
    attach_session_goal(session, goal.with_completed(frozenset({0, 1})))
    verdict = evaluate_session_goal(
        goal,
        _failed_deploy_result(),
        session=session,
        validate=_keep_ticks,
    )
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert verdict.reason == SessionGoalReason.TOOL_FAILED


def test_no_judge_complete_checklist_stays_active_when_evidence_overflowed() -> None:
    session = SessionCore()
    goal = SessionGoal(
        condition="checklist",
        checklist=("A", "B"),
        completed=frozenset({0, 1}),
        findings=("earlier work",),
        tool_evidence=None,
        tool_success_seen=True,
    )
    attach_session_goal(session, goal)
    verdict = evaluate_session_goal(
        goal,
        _result("Restored.", executed=1, success=1),
        session=session,
        validate=_keep_ticks,
    )
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert verdict.reason == SessionGoalReason.UNVERIFIED_OVERFLOW


def test_a_contradiction_vetoes_host_accept() -> None:
    session = SessionCore()
    goal = SessionGoal(condition="count rows", max_outer_turns=3)
    attach_session_goal(session, goal)
    verdict = evaluate_session_goal(
        goal,
        _result("All 5 checked. | 3 rows |", executed=1, success=1),
        session=session,
        judge=lambda **_kw: SessionGoalJudgeVerdict(
            verdict="NOT_REACHED",
            reason="Contradiction: the sentence says 5 but the table lists 3 rows",
            evidence_quote="3 rows",
        ),
    )
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert verdict.reason.startswith("Contradiction:")


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
        goal=SessionGoal(
            condition="finish migration",
            max_outer_turns=2,
            checklist=("patch the job", "verify the SHA filter"),
        ),
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


def test_unusable_judge_output_does_not_block_host_accept() -> None:
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
    assert status == SessionGoalStatus.ACHIEVED
    assert session.session_goal is not None
    assert session.session_goal.status == SessionGoalStatus.ACHIEVED


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


def test_without_a_judge_successful_tools_close_the_goal() -> None:
    session = SessionCore()
    open_goal = SessionGoal(condition="count users")
    attach_session_goal(session, open_goal)

    verdict = evaluate_session_goal(
        open_goal, _result("284 users.", executed=1, success=1), session=session
    )

    assert verdict.status == SessionGoalStatus.ACHIEVED
    assert verdict.reason == SessionGoalReason.ACHIEVED_TOOL_EVIDENCE


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


def test_a_judge_client_that_cannot_be_built_does_not_block_host_accept() -> None:
    def _broken_factory() -> object:
        raise RuntimeError("no llm configured")

    evaluate = build_session_goal_evaluator(_broken_factory)  # type: ignore[arg-type]
    session = SessionCore()
    goal = SessionGoal(condition="count users")
    attach_session_goal(session, goal)

    status = evaluate(goal, _result("284 users.", executed=1, success=1), session=session)

    assert status == SessionGoalStatus.ACHIEVED
    assert session.session_goal is not None
    assert session.session_goal.last_reason == SessionGoalReason.ACHIEVED_TOOL_EVIDENCE


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


def _evidence_result(text: str, tool_evidence: str) -> TurnResult:
    return TurnResult(
        "cli_agent_handled",
        ToolCallingTurnResult(
            1,
            1,
            1,
            False,
            True,
            tool_evidence=tool_evidence,
            evidence_success_count=1,
        ),
        text,
    )


def test_a_blocking_verdict_without_a_real_quote_keeps_the_goal_active() -> None:
    """The judge once blocked for a run failure the data never showed; now it must quote."""
    # Arrange: tool evidence exists, the judge says impossible but quotes nothing real.
    session = SessionCore()
    goal = SessionGoal(condition="did CI on the merged SHA fail then pass?", max_outer_turns=3)
    attach_session_goal(session, goal)
    result = _evidence_result(
        "No re-run found.",
        "Tool: gh\nArguments: {}\nOutcome: success\nResult: conclusion=success attempt=1",
    )

    # Act
    verdict = evaluate_session_goal(
        goal,
        result,
        session=session,
        judge=lambda **_kw: SessionGoalJudgeVerdict(
            verdict="IMPOSSIBLE",
            reason="the CI run failed and was then re-run",
            evidence_quote="conclusion=failure attempt=2",
        ),
    )

    # Assert: not impossible; the reason says the judge could not point at the data.
    assert verdict.status == SessionGoalStatus.ACTIVE
    assert verdict.reason.startswith(SessionGoalReason.JUDGE_UNSUPPORTED_PREFIX)


def test_reached_without_a_tool_quote_stays_active() -> None:
    """Run 7: the table said Yes; gh only showed attempt 1; judge still said met."""
    session = SessionCore()
    goal = SessionGoal(condition="which merged PRs were re-run to green", max_outer_turns=3)
    attach_session_goal(session, goal)
    result = _evidence_result(
        "| #6134 | Yes | CI and CodeQL |",
        "Tool: gh\nArguments: {}\nOutcome: success\nResult: conclusion=success attempt=1",
    )

    verdict = evaluate_session_goal(
        goal,
        result,
        session=session,
        judge=lambda **_kw: SessionGoalJudgeVerdict(
            verdict="GOAL_REACHED",
            reason="one PR was re-run to green",
            evidence_quote="Yes | CI and CodeQL",
        ),
    )

    assert verdict.status == SessionGoalStatus.ACTIVE
    assert verdict.reason.startswith(SessionGoalReason.JUDGE_UNSUPPORTED_PREFIX)


def test_reached_with_a_quote_from_the_tools_can_close() -> None:
    session = SessionCore()
    goal = SessionGoal(condition="which merged PRs were re-run to green", max_outer_turns=3)
    attach_session_goal(session, goal)

    verdict = evaluate_session_goal(
        goal,
        _evidence_result(
            "Only #6123 Release attempt 2.",
            "Tool: gh\nArguments: {}\nOutcome: success\nResult: run_attempt=2 conclusion=success",
        ),
        session=session,
        judge=lambda **_kw: SessionGoalJudgeVerdict(
            verdict="GOAL_REACHED",
            reason="Release was re-run to green",
            evidence_quote="run_attempt=2 conclusion=success",
        ),
    )

    assert verdict.status == SessionGoalStatus.ACHIEVED


def test_a_blocking_verdict_with_a_real_quote_is_kept() -> None:
    # Arrange: the quote appears in the tool evidence, whitespace aside.
    session = SessionCore()
    goal = SessionGoal(condition="close the ticket", max_outer_turns=3)
    attach_session_goal(session, goal)

    # Act
    verdict = evaluate_session_goal(
        goal,
        _evidence_result(
            "Cannot close it.",
            "Tool: gh\nArguments: {}\nOutcome: error\nResult: ticket   999 not found",
        ),
        session=session,
        judge=lambda **_kw: SessionGoalJudgeVerdict(
            verdict="IMPOSSIBLE",
            reason="the ticket does not exist",
            evidence_quote="ticket 999 not found",
        ),
    )

    # Assert
    assert verdict.status == SessionGoalStatus.IMPOSSIBLE
