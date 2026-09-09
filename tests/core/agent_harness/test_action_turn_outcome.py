"""Characterization of the action turn's outcome, field by field.

``_run_action_turn`` is one long function doing three unrelated jobs: building
the agent, running it, and turning what happened into a
``ToolCallingTurnResult`` plus console output.

These assert on complete values rather than single fields, so a refactor that
drops or reorders part of the composed text fails here rather than in a
customer's terminal.
"""

from __future__ import annotations

from typing import Any

from surfaces.interactive_shell.runtime.action_turn import run_action_tool_turn
from surfaces.interactive_shell.session import Session
from tests.core.agent.orchestration.action_execution_test_harness import (
    ActionExecutionHarness,
    FakeActionLLM,
    no_tool_response,
    tool_response,
)


def _console_text(harness: ActionExecutionHarness) -> str:
    return harness.console.file.getvalue()  # type: ignore[attr-defined]


def _outcome(result: Any) -> dict[str, Any]:
    """Every field the caller can observe, as one comparable value."""
    return {
        "planned_count": result.planned_count,
        "executed_count": result.executed_count,
        "executed_success_count": result.executed_success_count,
        "has_unhandled_clause": result.has_unhandled_clause,
        "handled": result.handled,
        "accounting_status": result.accounting_status,
    }


def test_a_turn_with_no_tool_calls_returns_the_agent_reply() -> None:
    # Arrange
    harness = ActionExecutionHarness(llm=FakeActionLLM([no_tool_response("just talking")]))

    # Act
    result = run_action_tool_turn(
        "hello", Session(), harness.console, llm_factory=harness.llm_factory
    )

    # Assert
    assert _outcome(result) == {
        "planned_count": 0,
        "executed_count": 0,
        "executed_success_count": 0,
        "has_unhandled_clause": False,
        "handled": False,
        "accounting_status": "completed",
    }
    assert "just talking" in _console_text(harness)


def test_final_text_that_reads_like_a_reply_becomes_the_response() -> None:
    """A substantial closing message is streamed as the user-facing answer."""
    # Arrange
    report = "## Findings\n\nThe checkout service is returning 502s.\nRoot cause: bad deploy."
    harness = ActionExecutionHarness(llm=FakeActionLLM([no_tool_response(report)]))

    # Act
    result = run_action_tool_turn(
        "what broke?", Session(), harness.console, llm_factory=harness.llm_factory
    )

    # Assert
    assert result.response_text == report


def test_a_terse_closing_line_is_the_answer_when_nothing_else_ran() -> None:
    """With no tool output there is nothing to preserve, so the text stands.

    Recorded because it is the mirror of the case above and easy to break when
    the composition order changes.
    """
    # Arrange
    harness = ActionExecutionHarness(llm=FakeActionLLM([no_tool_response("done")]))

    # Act
    result = run_action_tool_turn(
        "run it", Session(), harness.console, llm_factory=harness.llm_factory
    )

    # Assert
    assert result.response_text == "done"


def test_iteration_cap_is_preserved_on_turn_result() -> None:
    harness = ActionExecutionHarness(
        llm=FakeActionLLM([tool_response("skill_view", {"name": "missing"}) for _ in range(5)])
    )

    result = run_action_tool_turn(
        "keep trying",
        Session(),
        harness.console,
        llm_factory=harness.llm_factory,
    )

    assert result.hit_iteration_cap is True
    assert result.response_streamed is True
    assert "repeated tool calls produced no new result" in result.response_text
    assert _console_text(harness).count("repeated tool calls produced no new result") == 1
    assert harness.llm.invocations == 5


def test_a_menu_answered_this_turn_is_not_asked_again_through_the_real_turn() -> None:
    """The scope must carry the turn's message, or the answered-menu guard is blind.

    The guard lives in ``ask_user_choice`` but reads ``turn_user_message`` off
    the tool scope. The driver built that scope without the message, so the
    guard never fired in the shell while its own unit tests passed.
    """
    # Arrange: this turn's message is the answer; the model asks the same thing.
    title = "When should it run?"
    answer_turn = f"1. {title}\nWeekdays at 08:00 (recommended)"
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                tool_response(
                    "ask_user_choice",
                    {"title": title, "options": ["Weekdays at 08:00 (recommended)", "Every day"]},
                ),
                no_tool_response("Scheduling it for weekdays."),
            ]
        )
    )
    session = Session()

    # Act: a TTY turn, so a menu would really queue if the guard did not refuse.
    result = run_action_tool_turn(
        answer_turn, session, harness.console, llm_factory=harness.llm_factory, is_tty=True
    )

    # Assert: the call ran and was refused, so no menu was queued for a
    # question this turn's message already answered.
    assert (result.executed_count, result.executed_success_count) == (1, 0)
    assert session.pending_user_choice is None


def test_a_painted_tool_result_is_not_restated_in_the_closing() -> None:
    """A report painted during execution must not be printed a second time."""
    # Arrange: a tool result that says the console already shows it.
    from core.agent_harness.turns.display_text import format_generic_tool_payload
    from core.llm.types import ToolCall

    class _Result:
        details = {
            "rendered_in_shell": True,
            "summary": "acme/app: 8151 runs in 30 days, 1174 of 5727 PR runs failed.",
            "response_text": "acme/app: 8151 runs in 30 days.",
        }
        content = ""
        is_error = False

    call = ToolCall(id="1", name="analyze_github_ci_reliability", input={})

    # Act
    shown = format_generic_tool_payload(call, _Result())

    # Assert
    assert shown == ""


def _painted_result(*, final_text: str) -> Any:
    """A turn whose only tool painted its report straight to the console."""
    from core.llm.types import ToolCall

    class _ToolResult:
        details = {
            "rendered_in_shell": True,
            "summary": "acme/app: 8151 runs in 30 days.",
            "key_results": [
                {"label": "main branch red", "value": "36.4h of 30 days (5.1%)"},
                {"label": "CI-caused failures", "value": "333 of 1174 failed PR runs"},
                {"label": "Mean time back to green", "value": "1.0h"},
            ],
        }
        content = "{}"
        is_error = False

    call = ToolCall(id="1", name="analyze_github_ci_reliability", input={})

    class _Result:
        tool_results = [(call, _ToolResult())]
        executed = tool_results
        planned = [call]

    result = _Result()
    result.final_text = final_text  # type: ignore[attr-defined]
    return result


def test_a_closing_written_over_a_painted_report_is_dropped() -> None:
    """The model restates the figures, or contradicts them, without having seen them."""
    # Arrange
    from core.agent_harness.turns.action_driver import _compose_response, _TurnCounts

    session = Session()
    counts = _TurnCounts(
        executed_entries=[],
        executed_count=1,
        executed_success_count=1,
        generic_success_count=1,
        planned_count=1,
        handled=True,
    )

    # Act
    restatement = "36.4h red over 30 days, 333 of 1174 PR runs CI-caused, 1.0h back to green."
    _text, chunks, _use_final = _compose_response(
        _painted_result(final_text=restatement), session, counts
    )

    # Assert: the console already shows the report; nothing is added over it.
    assert chunks == []


def test_a_closing_question_survives_a_painted_report() -> None:
    """A question seeks direction; dropping it would leave the user with dead air."""
    # Arrange
    from core.agent_harness.turns.action_driver import _compose_response, _TurnCounts

    session = Session()
    counts = _TurnCounts(
        executed_entries=[],
        executed_count=1,
        executed_success_count=1,
        generic_success_count=1,
        planned_count=1,
        handled=True,
    )

    # Act
    _text, chunks, _use_final = _compose_response(
        _painted_result(final_text="Want me to break this down by workflow?"), session, counts
    )

    # Assert
    assert chunks == ["Want me to break this down by workflow?"]


def test_an_interpretation_of_a_painted_report_is_kept() -> None:
    """Only the duplicate goes: a judgement the table does not carry survives."""
    # Arrange
    from core.agent_harness.turns.action_driver import _compose_response, _TurnCounts

    session = Session()
    counts = _TurnCounts(
        executed_entries=[],
        executed_count=1,
        executed_success_count=1,
        generic_success_count=1,
        planned_count=1,
        handled=True,
    )
    judgement = "The Windows label job is your worst offender; I would quarantine it first."

    # Act
    _text, chunks, _use_final = _compose_response(
        _painted_result(final_text=judgement), session, counts
    )

    # Assert
    assert chunks == [judgement]


def test_a_plan_update_does_not_rescue_a_restated_closing() -> None:
    """The real demo turn updates its plan and paints the report in one go.

    Requiring every result to be painted let that turn keep a closing that
    repeated the figures, because ``update_plan`` is not a painted result.
    """
    # Arrange
    from core.agent_harness.turns.action_driver import _compose_response, _TurnCounts
    from core.llm.types import ToolCall

    class _Painted:
        details = {
            "rendered_in_shell": True,
            "key_results": [
                {"label": "main branch red", "value": "36.4h of 30 days (5.1%)"},
                {"label": "CI-caused failures", "value": "333 of 1174 failed PR runs"},
                {"label": "Mean time back to green", "value": "1.0h"},
            ],
        }
        content = "{}"
        is_error = False

    class _Plan:
        details = {"ok": True, "steps": []}
        content = "{}"
        is_error = False

    plan_call = ToolCall(id="1", name="update_plan", input={})
    report_call = ToolCall(id="2", name="analyze_github_ci_reliability", input={})

    class _Result:
        tool_results = [(plan_call, _Plan()), (report_call, _Painted())]
        executed = tool_results
        planned = [plan_call, report_call]
        final_text = "Main was red 36.4h (5.1%), with 333 of 1174 PR runs CI-caused; 1.0h to green."

    counts = _TurnCounts(
        executed_entries=[],
        executed_count=2,
        executed_success_count=2,
        generic_success_count=1,
        planned_count=2,
        handled=True,
    )

    # Act
    _text, chunks, _use_final = _compose_response(_Result(), Session(), counts)

    # Assert
    assert chunks == []
