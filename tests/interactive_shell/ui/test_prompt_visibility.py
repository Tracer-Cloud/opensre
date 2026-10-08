"""Typing box must hide while options / confirmation own the keyboard."""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from core.agent_harness.spi.handoff import AskUserQuestion
from core.agent_harness.spi.session_state import PendingUserChoice
from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState, TurnPhase
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.prompt_visibility import (
    clear_live_prompt_paint,
    typing_box_hidden,
)
from surfaces.interactive_shell.ui.terminal_ui import render_prompt_region
from surfaces.interactive_shell.ui.transcript import TranscriptRole


def _plain(ansi: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07", "", ansi)


def test_typing_box_hidden_while_awaiting_confirmation() -> None:
    session = Session()
    confirming = ReplState()
    confirming.phase = TurnPhase.AWAITING_CONFIRMATION
    confirming.confirm_prompt_text = "Approve this action?"

    assert typing_box_hidden(session, confirming) is True
    rendered = _plain(render_prompt_region(session, confirming, SpinnerState()).value)
    assert TranscriptRole.USER.value not in rendered
    assert "Approve this action?" in rendered


def test_typing_box_hidden_while_ask_user_options_pending() -> None:
    session = Session()
    session.pending_user_choice = PendingUserChoice(
        title="Ask User",
        options=("A", "B"),
        questions=(
            AskUserQuestion(label="Shape", title="Which shape?", options=("A", "B")),
            AskUserQuestion(label="Scope", title="Which scope?", options=("C", "D")),
        ),
    )

    assert typing_box_hidden(session, ReplState()) is True
    rendered = _plain(render_prompt_region(session, ReplState(), SpinnerState()).value)
    assert TranscriptRole.USER.value not in rendered


def test_typing_box_hidden_during_plan_with_ask_user_pending() -> None:
    """Plan overlay may stay; the free-text composer must not compete with Ask User."""
    from core.agent_harness.task_plan.plan import PlanStep, PlanStepStatus, TaskPlan

    session = Session()
    session.task_plan = TaskPlan(
        steps=(
            PlanStep(step="Clarify blockers", status=PlanStepStatus.IN_PROGRESS),
            PlanStep(step="Write diagnosis", status=PlanStepStatus.PENDING),
        )
    )
    session.pending_user_choice = PendingUserChoice(
        title="Ask User",
        options=("A", "B"),
        questions=(
            AskUserQuestion(label="Shape", title="Which shape?", options=("A", "B")),
            AskUserQuestion(label="Scope", title="Which scope?", options=("C", "D")),
        ),
    )

    assert typing_box_hidden(session, ReplState()) is True
    rendered = _plain(render_prompt_region(session, ReplState(), SpinnerState()).value)
    assert TranscriptRole.USER.value not in rendered
    assert "Clarify blockers" in rendered or "Plan" in rendered


def test_typing_box_hidden_while_exclusive_stdin_menu_active() -> None:
    session = Session()
    session.terminal.exclusive_stdin_active = True
    assert typing_box_hidden(session, ReplState()) is True
    rendered = _plain(render_prompt_region(session, ReplState(), SpinnerState()).value)
    assert TranscriptRole.USER.value not in rendered


def test_idle_prompt_still_shows_typing_box() -> None:
    session = Session()
    assert typing_box_hidden(session, ReplState()) is False
    rendered = _plain(render_prompt_region(session, ReplState(), SpinnerState()).value)
    assert TranscriptRole.USER.value in rendered


def test_confirmation_region_height_is_stable_across_selection_changes() -> None:
    # Confirmation is a taller modal block than the idle prompt, so entering it
    # shifts height once. What must stay constant is height WHILE confirming —
    # moving the arrow selection (Yes -> No) must not resize the region.
    session = Session()
    spinner = SpinnerState()

    yes_state = ReplState()
    yes_state.phase = TurnPhase.AWAITING_CONFIRMATION
    yes_state.confirm_prompt_text = "Approve this action?"
    yes_state.confirm_selected = 0
    yes_rows = render_prompt_region(session, yes_state, spinner).value.count("\n")

    no_state = ReplState()
    no_state.phase = TurnPhase.AWAITING_CONFIRMATION
    no_state.confirm_prompt_text = "Approve this action?"
    no_state.confirm_selected = 1
    no_rows = render_prompt_region(session, no_state, spinner).value.count("\n")

    assert yes_rows == no_rows


def test_streaming_prompt_height_matches_idle_with_live_tool_on_status_row() -> None:
    """Message region is lead blank → status → seam; no reserved action gap.

    Idle omits the empty status placeholder (that was the big gap under the
    banner). Thinking/Invoking add one status line, but never a second reserved
    action row. Auto chrome is not counted here — it renders on a fixed row
    under the composer.
    """
    session = Session()
    idle = SpinnerState()
    idle_rows = render_prompt_region(session, ReplState(), idle).value.count("\n")

    spinner = SpinnerState()
    spinner.start()
    spinner.set_phase(SpinnerState.THINKING_PHASE)
    thinking_rows = render_prompt_region(session, ReplState(), spinner).value.count("\n")
    # Busy adds a lead blank, the Thinking row, and the seam above the composer.
    assert thinking_rows == idle_rows + 3

    spinner.set_phase(SpinnerState.INVOKING_TOOLS_PHASE)
    spinner.set_active_action("GitHub CLI · gh api repos/x", action_id="t1")
    filled = render_prompt_region(session, ReplState(), spinner).value
    assert filled.count("\n") == thinking_rows
    plain = _plain(filled)
    assert "GitHub CLI" in plain
    assert "Invoking tools" in plain


def test_prompt_region_idle_is_just_the_composer() -> None:
    """Idle reserves no chrome rows above the box.

    Permission chrome moved to its own fixed row under the composer, so an
    idle message region that grows a row again is the old gap-under-the-banner
    regression coming back.
    """
    session = Session()
    idle = _plain(render_prompt_region(session, ReplState(), SpinnerState()).value)
    assert not idle.startswith("\n")
    assert "\n" not in idle
    assert "Auto (High)" not in idle
    assert idle.startswith(f" {TranscriptRole.USER.value}")


def test_prompt_region_thinking_leads_with_a_blank_row() -> None:
    """Thinking is separated from transcript and quieter composer metadata."""
    session = Session()
    spinner = SpinnerState()
    spinner.start()
    spinner.set_phase(SpinnerState.THINKING_PHASE)
    busy = render_prompt_region(session, ReplState(), spinner).value
    assert busy.startswith("\n")
    plain = _plain(busy)
    assert "Thinking" in plain
    # Lead blank, then the status row (spinner glyph + Thinking…) — not flush.
    first_content_line = plain.split("\n", 1)[1]
    assert "Thinking" in first_content_line
    assert not plain.startswith("\n\n")
    lines = plain.splitlines()
    assert lines[1].startswith("  ")
    # The seam: one blank row between the live region and the composer.
    assert lines[2] == ""
    assert lines[3].startswith(f" {TranscriptRole.USER.value}")


def test_idle_prompt_has_no_recurring_ready_hint() -> None:
    # Command hints live on ``?``, not a per-turn "Ready · …" line (which also
    # stacked into copies on resize).
    session = Session()
    rendered = _plain(render_prompt_region(session, ReplState(), SpinnerState()).value)
    assert "Ready" not in rendered
    assert "/ for commands" not in rendered


def test_confirmation_region_shows_stacked_yes_no_choice() -> None:
    session = Session()
    state = ReplState()
    state.phase = TurnPhase.AWAITING_CONFIRMATION
    state.confirm_prompt_text = "Approve this action?"
    state.confirm_selected = 1

    rendered = _plain(render_prompt_region(session, state, SpinnerState()).value)

    assert "[a] Yes" in rendered
    assert "[b] No" in rendered
    # The selected row (No) carries the arrow; the typing box is hidden.
    assert "❯ [b] No" in rendered
    assert TranscriptRole.USER.value not in rendered


def test_clear_live_prompt_paint_erases_only_the_app_region() -> None:
    # ``erase`` wipes the app's own rows (leaving the transcript) so the menu
    # draws inline like Droid; a full ``clear`` would read as a new window.
    session = Session()
    renderer = MagicMock()
    app = MagicMock()
    app.renderer = renderer
    app.is_running = True
    session.terminal.prompt_app = app

    clear_live_prompt_paint(session)

    renderer.erase.assert_called_once()
    renderer.clear.assert_not_called()
    app.invalidate.assert_called_once()
