"""Auto-status ANSI line: permission copy, no model slug on idle chrome."""

from __future__ import annotations

from config.constants.repl_autonomy import AutoLevel
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.auto_status import auto_status_ansi


def test_high_auto_shows_allow_all_permission() -> None:
    """High is the default — the bar must still say what High permits."""
    import re

    from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
    from surfaces.interactive_shell.ui.terminal_ui import render_prompt_region

    session = Session()
    session.terminal.auto_level = AutoLevel.HIGH
    plain = re.sub(
        r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07",
        "",
        render_prompt_region(session, ReplState(), SpinnerState()).value,
    )
    assert "Auto (High)" in plain
    assert "Allow all" in plain
    session.terminal.auto_level = AutoLevel.MED
    med = re.sub(
        r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07",
        "",
        render_prompt_region(session, ReplState(), SpinnerState()).value,
    )
    assert "Auto (Med)" in med
    assert "Ask shell + edits" in med


def test_idle_auto_status_omits_the_model_slug() -> None:
    """Model lives on ``/model`` and ``?``, not every idle frame."""
    session = Session()
    session.terminal.auto_level = AutoLevel.HIGH
    rendered = auto_status_ansi(session)
    assert "Auto (High)" in rendered
    assert "Allow all" in rendered
    assert "gpt-" not in rendered


def test_busy_keeps_dim_auto_under_thinking() -> None:
    """Thinking owns the gold accent; Auto stays DIM so permission does not vanish."""
    import re

    import infrastructure.terminal.theme as ui_theme
    from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
    from surfaces.interactive_shell.ui.terminal_ui import render_prompt_region

    ui_theme.set_active_theme("amber")
    session = Session()
    session.terminal.auto_level = AutoLevel.HIGH
    loud = auto_status_ansi(session, quiet=False)
    quiet = auto_status_ansi(session, quiet=True)
    assert ui_theme.BOLD_REPLY_MARKER_ANSI in loud
    assert ui_theme.BOLD_REPLY_MARKER_ANSI not in quiet
    assert ui_theme.DIM_ANSI in quiet
    spinner = SpinnerState()
    spinner.start()
    spinner.set_phase(SpinnerState.THINKING_PHASE)
    rendered = render_prompt_region(session, ReplState(), spinner).value
    plain = re.sub(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07", "", rendered)
    assert "Thinking" in plain
    assert "Auto (High)" in plain
    assert "Allow all" in plain
    assert plain.index("Thinking") < plain.index("Auto (High)")
    assert ui_theme.DIM_ANSI + "Auto (High)" in rendered


def test_render_prompt_region_shows_the_auto_status_line() -> None:
    """The live prompt composition must reach ``auto_status_ansi`` so the level shows."""
    import re

    from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
    from surfaces.interactive_shell.ui.terminal_ui import render_prompt_region

    session = Session()
    session.terminal.auto_level = AutoLevel.MED

    rendered = render_prompt_region(session, ReplState(), SpinnerState()).value
    plain = re.sub(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07", "", rendered)

    assert "Auto (Med)" in plain


def test_confirmation_region_height_is_constant_while_confirming() -> None:
    """The Yes/No block is a taller modal than the idle prompt, but its own
    height must not change as the arrow selection moves between the options."""
    from surfaces.interactive_shell.runtime.core.state import (
        ReplState,
        SpinnerState,
        TurnPhase,
    )
    from surfaces.interactive_shell.ui.terminal_ui import render_prompt_region

    session = Session()
    spinner = SpinnerState()

    def _confirm_rows(selected: int) -> int:
        state = ReplState()
        state.phase = TurnPhase.AWAITING_CONFIRMATION
        state.confirm_prompt_text = "Approve this action?"
        state.confirm_selected = selected
        return render_prompt_region(session, state, spinner).value.count("\n")

    assert _confirm_rows(0) == _confirm_rows(1)
