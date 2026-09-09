"""Checklist success criteria on SessionGoal (tool ticks, not prose tags)."""

from __future__ import annotations

from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.session_goal.evaluate import (
    evaluate_session_goal,
)
from core.agent_harness.session_goal.goal import (
    SessionGoal,
    SessionGoalStatus,
    attach_session_goal,
    build_session_goal,
    mark_session_goal_started,
)
from core.agent_harness.session_goal.judge import SessionGoalJudgeVerdict
from core.agent_harness.session_goal.progress import format_session_goal_progress
from core.agent_harness.session_goal.run_until import run_until_session_goal
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult


def _keep_ticks(**kw: object) -> frozenset[int]:
    newly = kw.get("newly")
    return newly if isinstance(newly, frozenset) else frozenset()


def _tick_next(session: SessionCore, text: str) -> TurnResult:
    stored = session.session_goal
    if isinstance(stored, SessionGoal):
        nxt = len(stored.completed)
        if nxt < len(stored.checklist):
            attach_session_goal(session, stored.with_completed(stored.completed | {nxt}))
    return TurnResult(
        final_intent="cli_agent_handled",
        action_result=ToolCallingTurnResult(
            planned_count=1,
            executed_count=1,
            executed_success_count=1,
            has_unhandled_clause=False,
            handled=True,
        ),
        assistant_response_text=text,
    )


def test_build_session_goal_preserves_structured_checklist() -> None:
    goal = build_session_goal(
        condition="run the checklist",
        checklist=("List the goal", "Name step one", "Confirm done"),
        max_outer_turns=5,
    )

    assert goal.checklist == ("List the goal", "Name step one", "Confirm done")
    assert goal.max_outer_turns == 5
    assert goal.step_count == 3


def test_format_session_goal_progress_shows_checklist() -> None:
    from core.agent_harness.session_goal.progress import format_session_goal_progress

    goal = SessionGoal(
        condition="ship the checklist",
        turns_used=2,
        max_outer_turns=5,
        checklist=("One", "Two", "Three"),
        completed=frozenset({0}),
        last_reason="checklist 1/3 done — next: Two",
    )
    rendered = format_session_goal_progress(goal)

    assert "◎ /goal active" in rendered
    assert "turn 2/5" in rendered
    assert "condition: ship the checklist" in rendered
    assert "reason: checklist 1/3 done — next: Two" in rendered
    assert "One" in rendered and "Two" in rendered and "Three" in rendered
    assert "[x]" in rendered
    assert "[ ]" in rendered
    assert "→" in rendered  # next unfinished item marker


def test_format_session_goal_status_line_is_compact() -> None:
    from core.agent_harness.session_goal.progress import format_session_goal_status_line

    goal = SessionGoal(
        condition="two-step",
        turns_used=1,
        max_outer_turns=3,
        checklist=("one", "two"),
        completed=frozenset({0}),
    )
    line = format_session_goal_status_line(goal)
    assert "\n" not in line
    assert "◎ /goal active" in line
    assert "turn 1/3" in line
    assert "next: two" in line


def test_format_session_goal_progress_active_header_includes_duration() -> None:
    from core.agent_harness.session_goal.goal import mark_session_goal_started
    from core.agent_harness.session_goal.progress import format_session_goal_progress

    goal = mark_session_goal_started(
        SessionGoal(condition="finish migrate", max_outer_turns=5, turns_used=1),
        now=1_000.0,
        input_tokens=10,
        output_tokens=5,
    )
    rendered = format_session_goal_progress(
        goal,
        now=1_083.0,
        input_tokens=110,
        output_tokens=55,
    )
    assert "◎ /goal active" in rendered
    assert "1m 23s" in rendered or "83s" in rendered
    assert "turn 1/5" in rendered
    assert "tokens" in rendered.lower()
    assert "+150" in rendered or "150" in rendered


def test_format_duration_and_token_compacts() -> None:
    from core.agent_harness.session_goal.progress import (
        format_duration_compact,
        format_token_count_compact,
    )

    assert format_duration_compact(45) == "45s"
    assert format_duration_compact(83) == "1m 23s"
    assert format_duration_compact(3725) == "1h 02m"
    assert format_token_count_compact(150) == "150"
    assert format_token_count_compact(1200) == "1.2k"
    assert format_token_count_compact(3_400_000) == "3.4M"


def test_format_token_count_compact_rolls_over_at_the_million_boundary() -> None:
    """A count that rounds to 1000k at one-decimal precision must read as 1M."""
    from core.agent_harness.session_goal.progress import format_token_count_compact

    assert format_token_count_compact(999_949) == "999.9k"
    assert format_token_count_compact(999_950) == "1M"
    assert format_token_count_compact(999_999) == "1M"
    assert format_token_count_compact(1_000_000) == "1M"


def test_session_goal_payload_round_trips_started_at_and_token_baseline() -> None:
    from core.agent_harness.session_goal.goal import mark_session_goal_started
    from core.agent_harness.session_goal.persist import (
        session_goal_from_payload,
        session_goal_to_payload,
    )

    goal = mark_session_goal_started(
        SessionGoal(condition="persist clocks", max_outer_turns=2),
        now=1_700_000_000.0,
        input_tokens=11,
        output_tokens=7,
    )
    restored = session_goal_from_payload(session_goal_to_payload(goal))
    assert restored is not None
    assert restored.started_at == 1_700_000_000.0
    assert restored.token_baseline_input == 11
    assert restored.token_baseline_output == 7


def test_outer_loop_achieves_via_checklist_without_achieved_tag() -> None:
    session = SessionCore()
    turns: list[str] = []
    goal = SessionGoal(
        condition="three checks",
        max_outer_turns=5,
        checklist=("A", "B", "C"),
    )
    attach_session_goal(session, goal)

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        return _tick_next(session, "Working.")

    outcome = run_until_session_goal(
        _chat,
        session,
        "go",
        goal=goal,
        evaluate=lambda goal, result, *, session=None: (
            evaluate_session_goal(
                goal,
                result,
                session=session,
                validate=_keep_ticks,
            ).status
        ),
    )

    assert len(turns) == 3
    assert outcome.goal.status == SessionGoalStatus.ACHIEVED
    assert outcome.goal.completed == frozenset({0, 1, 2})


def test_prompt_lists_unfinished_checklist_items() -> None:
    from core.agent_harness.session_goal.continuation import continuation_prompt

    goal = SessionGoal(
        condition="x",
        checklist=("A", "B", "C"),
        completed=frozenset({0}),
        last_reason="checklist 1/3 done — next: B",
    )
    prompt = continuation_prompt(goal)

    assert "B" in prompt and "C" in prompt
    assert "session_goal_complete" in prompt
    assert "Last progress: checklist 1/3 done — next: B" in prompt


def test_outer_loop_prompt_carries_reason_after_partial_progress() -> None:
    session = SessionCore()
    turns: list[str] = []
    goal = SessionGoal(
        condition="two checks",
        max_outer_turns=4,
        checklist=("A", "B"),
    )
    attach_session_goal(session, goal)

    def _chat(message: str) -> TurnResult:
        turns.append(message)
        return _tick_next(session, "Working.")

    run_until_session_goal(
        _chat,
        session,
        "go",
        goal=goal,
        evaluate=lambda goal, result, *, session=None: (
            evaluate_session_goal(
                goal,
                result,
                session=session,
                judge=lambda **kw: SessionGoalJudgeVerdict(
                    verdict="NOT_REACHED" if kw["unfinished"] else "GOAL_REACHED",
                    reason="checklist 1/2 done — next: B",
                ),
                validate=_keep_ticks,
            ).status
        ),
    )

    assert len(turns) == 2
    assert "Last progress:" in turns[1]
    assert "next: B" in turns[1]


def test_finished_goal_headline_keeps_elapsed_time_and_tokens() -> None:
    # Arrange: an achieved goal stamped at t=1000 with a 10+2 token baseline.
    goal = mark_session_goal_started(
        SessionGoal(condition="count users", status=SessionGoalStatus.ACHIEVED, turns_used=1),
        now=1_000.0,
        input_tokens=10,
        output_tokens=2,
    )

    # Act
    text = format_session_goal_progress(
        goal, now=1_045.0, input_tokens=1_010, output_tokens=202, include_condition=False
    )

    # Assert: the headline reads like the active one; the condition is not repeated.
    assert text.startswith("◎ /goal achieved · 45s · turn 1/5 · +1.2k tokens")
    assert "condition:" not in text


def test_strip_shell_prompt_chrome_removes_repeated_prompt_prefix() -> None:
    from core.agent_harness.session_goal.goal import strip_shell_prompt_chrome

    assert (
        strip_shell_prompt_chrome(
            "[1] ❯ [1] ❯ what windows users number did open opensre during last 7 days?"
        )
        == "what windows users number did open opensre during last 7 days?"
    )
    assert strip_shell_prompt_chrome("bare question") == "bare question"


def test_attach_session_goal_strips_prompt_chrome_from_condition() -> None:
    session = SessionCore()
    attached = attach_session_goal(
        session,
        SessionGoal(
            condition="[1] ❯ what windows users number did open opensre during last 7 days?",
            max_outer_turns=3,
        ),
    )
    assert attached.condition == ("what windows users number did open opensre during last 7 days?")


def test_is_session_goal_progress_text_uses_progress_constants() -> None:
    from core.agent_harness.session_goal.goal import SessionGoalReason
    from core.agent_harness.session_goal.progress import (
        SESSION_GOAL_PROGRESS_MARK,
        SESSION_GOAL_USER_WORD,
        is_session_goal_progress_text,
    )

    progress_text = (
        f"{SESSION_GOAL_PROGRESS_MARK} {SESSION_GOAL_USER_WORD} active · 0s · turn 0/4\n"
        f"  reason: {SessionGoalReason.WAITING_HOST_SIGNAL}"
    )
    assert is_session_goal_progress_text(progress_text) is True
    assert is_session_goal_progress_text(SessionGoalReason.WAITING_HOST_SIGNAL) is True
    assert is_session_goal_progress_text("272 Windows users last 7 days.") is False
