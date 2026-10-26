"""A session-goal continuation turn must know what earlier turns established.

Observed live: turn 1 fetched weather and news successfully and printed the
figures; a later turn ran one slash command, saw no weather tool among *its*
results, and answered "current weather and news retrieval is unavailable in
this environment". The work had been done and the agent reported that it had
not been.

Each continuation is a fresh ``chat`` call carrying only the nudge, and
``record_conversation_turn`` stores assistant prose — never tool payloads. So
nothing hands turn N+1 the evidence turn N gathered.
"""

from __future__ import annotations

from core.agent_harness.session.session_core import SessionCore
from core.agent_harness.session_goal.evaluate import evaluate_session_goal
from core.agent_harness.session_goal.goal import SessionGoal
from core.agent_harness.session_goal.judge import SessionGoalJudgeVerdict
from core.agent_harness.session_goal.run_until import run_until_session_goal
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult


def _turn(text: str, *, executed: int = 0, success: int = 0) -> TurnResult:
    return TurnResult(
        final_intent="cli_agent_fallback",
        action_result=ToolCallingTurnResult(
            planned_count=executed,
            executed_count=executed,
            executed_success_count=success,
            has_unhandled_clause=False,
            handled=True,
        ),
        assistant_response_text=text,
    )


def test_continuation_turn_is_told_what_earlier_turns_gathered() -> None:
    """The second turn's input must carry the first turn's evidence.

    Without it the second turn can only see its own tools, and an absence there
    reads as an absence overall — which is how a completed retrieval was
    reported as unavailable.
    """
    # Arrange — turn 1 gathers real evidence, turn 2 gathers nothing.
    session = SessionCore()
    prompts: list[str] = []

    def _chat(message: str) -> TurnResult:
        prompts.append(message)
        if len(prompts) == 1:
            return _turn("Antarctica is -32C and Hawaii is 24C.", executed=2, success=2)
        return _turn("Checked the Slack integration.", executed=1, success=1)

    # Act — two turns of one goal.
    run_until_session_goal(
        _chat,
        session,
        "weather brief for Antarctica and Hawaii, then send it",
        goal=SessionGoal(
            condition="weather brief for Antarctica and Hawaii, then send it",
            max_outer_turns=3,
            checklist=("fetch the weather", "correlate news", "send the brief"),
        ),
        evaluate=lambda goal, result, *, session=None: (
            evaluate_session_goal(
                goal,
                result,
                session=session,
                judge=lambda **_kw: SessionGoalJudgeVerdict(
                    verdict="NOT_REACHED", reason="brief not sent yet"
                ),
            ).status
        ),
    )

    # Assert — turn 2's prompt must reference what turn 1 established.
    assert len(prompts) >= 2, "the goal loop did not run a continuation turn"
    continuation = prompts[1]
    assert "-32C" in continuation or "Antarctica is" in continuation, (
        "the continuation turn was not given the evidence turn 1 gathered, so it "
        "can only reason from its own tools:\n" + continuation
    )
