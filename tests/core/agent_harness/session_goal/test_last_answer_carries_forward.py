"""A continuation turn is told what the previous turn already answered.

Asked "how many github actions runs failed in the last 24 hours?", turn 1
answered "73 out of 800 completed" from conversation history — no tool ran. That
left no ``finding`` (findings require tool evidence), so turn 2 re-derived the
number by another route, reported 12, and closed the goal. The user saw two
counts for one question with nothing reconciling them.

``last_answer`` is the weaker channel: recorded whether or not a tool ran, and
rendered as "already told the user" rather than as established fact, so an
unverified answer cannot become settled by being repeated.
"""

from __future__ import annotations

from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.session_goal.continuation import continuation_prompt
from core.agent_harness.session_goal.evaluate import evaluate_session_goal
from core.agent_harness.session_goal.goal import SessionGoal
from core.agent_harness.session_goal.judge import SessionGoalJudgeVerdict
from core.agent_harness.session_goal.run_until import run_until_session_goal
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult

_ANSWER = "73 GitHub Actions runs failed out of 800 completed, last 24 hours."


def _goal() -> SessionGoal:
    return SessionGoal(condition="how many github actions runs failed?", max_outer_turns=5)


def test_the_previous_answer_reaches_the_next_turn() -> None:
    # Arrange
    goal = _goal().with_last_answer(_ANSWER)

    # Act
    prompt = continuation_prompt(goal)

    # Assert
    assert _ANSWER in prompt


def test_the_next_turn_is_told_to_explain_a_different_number() -> None:
    # Arrange / Act
    prompt = continuation_prompt(_goal().with_last_answer(_ANSWER))

    # Assert: silently replacing it is the failure being prevented.
    assert "say why" in prompt


def test_a_previous_answer_is_not_presented_as_established() -> None:
    """``findings`` means established and says "treat these as done".

    An answer with no tool behind it must not borrow that authority, or a wrong
    remembered number becomes settled fact by being repeated.
    """
    # Arrange / Act
    goal = _goal().with_last_answer(_ANSWER)

    # Assert
    assert goal.findings == ()
    assert "treat these as done" not in continuation_prompt(goal)


def test_an_empty_answer_is_not_recorded() -> None:
    assert _goal().with_last_answer("   ").last_answer == ""


def test_a_later_answer_replaces_the_earlier_one() -> None:
    # Arrange / Act: only the most recent reply is worth reconciling against.
    goal = _goal().with_last_answer(_ANSWER).with_last_answer("12 runs failed.")

    # Assert
    assert goal.last_answer == "12 runs failed."


def test_a_tool_less_turn_still_hands_its_answer_to_the_next_turn() -> None:
    """The wiring, not just the helper: no tool ran, yet turn 2 is told the answer.

    Gating this on tool evidence is what produced 73 then 12. ``findings``
    stays gated; this must not be.
    """
    # Arrange: two turns, neither running a tool, the first reporting a count.
    session = SessionCore()
    prompts: list[str] = []

    def _chat(message: str) -> TurnResult:
        prompts.append(message)
        body = _ANSWER if len(prompts) == 1 else "12 runs failed. session_goal:achieved"
        return TurnResult(
            final_intent="cli_agent_fallback",
            action_result=ToolCallingTurnResult(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text=body,
        )

    # Act
    run_until_session_goal(
        _chat,
        session,
        "how many github actions runs failed in the last 24 hours?",
        goal=SessionGoal(
            condition="how many github actions runs failed in the last 24 hours?",
            max_outer_turns=3,
        ),
        evaluate=lambda goal, result, *, session=None: (
            evaluate_session_goal(
                goal,
                result,
                session=session,
                judge=lambda **_kw: SessionGoalJudgeVerdict(
                    verdict="NOT_REACHED", reason="need a live Actions query"
                ),
            ).status
        ),
    )

    # Assert: the second prompt carries what the first turn said.
    assert len(prompts) >= 2, "the goal should have run a continuation turn"
    assert _ANSWER in prompts[1]
