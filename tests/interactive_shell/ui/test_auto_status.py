"""Auto-status ANSI line: permission copy, no model slug on idle chrome."""

from __future__ import annotations

from config.constants.repl_autonomy import AutoLevel
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.auto_status import auto_status_ansi


def test_high_auto_shows_allow_all_permission() -> None:
    """High is the default — the bar must still say what High permits."""
    import re

    from surfaces.interactive_shell.ui.ci_fix_status import prompt_status_ansi

    session = Session()
    session.terminal.auto_level = AutoLevel.HIGH
    plain = re.sub(
        r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07",
        "",
        prompt_status_ansi(session),
    )
    assert "Auto (High)" in plain
    assert "Allow all" in plain
    session.terminal.auto_level = AutoLevel.MED
    med = re.sub(
        r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07",
        "",
        prompt_status_ansi(session),
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
    # Auto no longer rides the message region: it renders on its own fixed row
    # under the composer, quiet while the spinner above owns the accent.
    assert "Auto (High)" not in plain
    assert ui_theme.DIM_ANSI + "    Auto (High)" in quiet


def test_status_line_renders_below_the_composer_at_a_fixed_height() -> None:
    """Auto chrome is a one-row window *under* the box, in every state.

    A height that changes with session state is what misplaces the cursor and
    strands stale rows below the input (the reason the prompt_toolkit bottom
    toolbar stays collapsed), so the row never wraps and never exceeds one row.
    It must still allow ``min=0``: a short window has to collapse this chrome
    rather than refuse to draw ("Window too small") and lose the composer.
    """
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import DummyInput
    from prompt_toolkit.layout.containers import FloatContainer, HSplit, Window
    from prompt_toolkit.output import DummyOutput

    from surfaces.interactive_shell.ui import input_prompt

    with create_app_session(input=DummyInput(), output=DummyOutput()):
        prompt = input_prompt.build_prompt_session(status_line=lambda: "Auto (Med)")

    root = prompt.layout.container
    assert isinstance(root, HSplit)
    framed_input = root.children[0]
    assert isinstance(framed_input, FloatContainer)
    chrome = framed_input.content.children[0]
    assert isinstance(chrome, HSplit)
    # before_input, composer, then the settled status row — last, under the box.
    assert len(chrome.children) == 3
    status_row = chrome.children[-1]
    assert isinstance(status_row, Window)
    assert status_row.height.preferred == 1
    assert status_row.height.max == 1
    # Collapsible under vertical pressure, so a short window keeps the composer.
    assert status_row.height.min == 0
    assert not status_row.wrap_lines()
    assert status_row.content.text().value == "Auto (Med)"


def test_injected_prompt_session_keeps_status_in_the_message_region() -> None:
    """A caller-supplied session has no status row, so chrome stays above the box.

    ``create_repl_runtime(pt_session=…)`` and ``InteractiveShellController(
    pt_session=…)`` skip the branch that frames the prompt, so the status row
    is never installed on those sessions. Without this fallback they lose the
    permission and repair chrome for their entire lifetime.
    """
    import re

    from prompt_toolkit import PromptSession
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import DummyInput
    from prompt_toolkit.output import DummyOutput

    from surfaces.interactive_shell.runtime.core.prompt_builder import PromptBuilder
    from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState

    session = Session()
    session.terminal.auto_level = AutoLevel.HIGH
    with create_app_session(input=DummyInput(), output=DummyOutput()):
        injected: PromptSession[str] = PromptSession()
        builder = PromptBuilder(session, ReplState(), SpinnerState(), pt_session=injected)
        assert not builder._status_row_installed
        plain = re.sub(
            r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07",
            "",
            builder.message_with_spinner().value,
        )

    assert "Auto (High)" in plain
    assert "Allow all" in plain


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
