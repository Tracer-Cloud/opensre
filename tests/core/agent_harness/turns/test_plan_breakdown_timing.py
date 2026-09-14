"""The one-shot plan breakdown waits while a blocked step is being resolved with the user."""

from __future__ import annotations

from core.agent_harness.task_plan.plan import parse_task_plan
from core.agent_harness.task_plan.update_plan_policy import apply_update_plan_session
from core.agent_harness.turns.action_driver import _show_completed_plan_breakdown
from core.agent_harness.turns.headless_adapters import BufferOutputSink
from surfaces.interactive_shell.session import Session


def _settled_blocked_session() -> Session:
    session = Session()
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Inspect repository", "status": "blocked"},
                {"step": "Summarize results", "status": "blocked"},
            ],
            "explanation": "The user forbade running commands.",
        }
    )
    assert error is None and plan is not None
    apply_update_plan_session(session, plan, plan_only=False)
    return session


def test_no_plan_ended_while_the_answer_to_the_resolution_question_is_on_its_way() -> None:
    """Live: "Plan ended · 0/2 · 2 blocked" printed right under the menu the user was answering."""
    # Arrange: the plan settled on blocked steps and the shell awaits the menu answer.
    session = _settled_blocked_session()
    session.terminal.awaiting_handoff_answer = True
    output = BufferOutputSink()

    # Act
    _show_completed_plan_breakdown(output, session)

    # Assert: nothing printed, and the breakdown is still owed once the plan really ends.
    assert output.text == ""
    session.terminal.awaiting_handoff_answer = False
    _show_completed_plan_breakdown(output, session)
    assert "Plan ended" in output.text
