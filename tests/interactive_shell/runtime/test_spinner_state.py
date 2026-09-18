"""SpinnerState: phase status row, live tool fold-in, idle Ready hint."""

from __future__ import annotations

import re
import time

from surfaces.interactive_shell.runtime.core.state import SpinnerState


def test_inline_spinner_empty_when_idle() -> None:
    assert SpinnerState().inline_spinner_ansi() == ""


def test_inline_spinner_includes_phase_and_stop_hint() -> None:
    spinner = SpinnerState()
    spinner.start()
    spinner.set_phase(SpinnerState.THINKING_PHASE)
    rendered = re.sub(r"\x1b\[[0-9;]*m", "", spinner.inline_spinner_ansi())
    assert SpinnerState.THINKING_PHASE in rendered
    assert "Press ESC to stop" in rendered


def test_status_row_uses_single_spaces_around_elapsed_badge() -> None:
    """Hint and badge sit one cell apart and the badge hugs its brackets."""
    spinner = SpinnerState()
    spinner.start()
    spinner.set_phase(SpinnerState.INVOKING_TOOLS_PHASE)
    spinner.set_active_action("scan github ci health")
    rendered = re.sub(r"\x1b\[[0-9;]*m", "", spinner.inline_spinner_ansi())
    assert re.search(r"scan github ci health \(Press ESC to stop\) \[\d+s\]$", rendered)
    assert "  " not in rendered

    spinner.bytes_in = 40_000
    rendered = re.sub(r"\x1b\[[0-9;]*m", "", spinner.inline_spinner_ansi())
    assert re.search(r"\(Press ESC to stop\) \[\d+s · ↓ \S+ tokens\]$", rendered)
    assert "  " not in rendered


def test_long_phase_clips_to_one_prompt_column_budget() -> None:
    """A long phase label must not soft-wrap past the one reserved prompt row."""
    from surfaces.shared.terminal.prompt_layout import prompt_line_width, prompt_text_width

    spinner = SpinnerState()
    spinner.start()
    spinner.set_phase("X" * 400)
    rendered = re.sub(r"\x1b\[[0-9;]*m", "", spinner.inline_spinner_ansi())

    assert prompt_text_width(rendered) <= prompt_line_width()


def test_live_tool_folds_into_spinner_row_and_clears() -> None:
    """Running tool appears on the same status row as the phase; clear drops it."""
    spinner = SpinnerState()
    spinner.start()
    spinner.set_phase(SpinnerState.INVOKING_TOOLS_PHASE)
    before = re.sub(r"\x1b\[[0-9;]*m", "", spinner.inline_spinner_ansi())
    assert "Execute · cd /tmp" not in before

    spinner.set_active_action("Execute · cd /tmp")
    rendered = re.sub(r"\x1b\[[0-9;]*m", "", spinner.inline_spinner_ansi())
    assert "Invoking tools…" in rendered
    assert "Execute · cd /tmp" in rendered

    spinner.clear_active_action()
    cleared = re.sub(r"\x1b\[[0-9;]*m", "", spinner.inline_spinner_ansi())
    assert "Execute · cd /tmp" not in cleared


def test_active_action_stack_keeps_earlier_tools_until_they_end() -> None:
    """A later start must not overwrite an earlier still-running tool."""
    spinner = SpinnerState()
    spinner.start()
    spinner.set_active_action("GitHub CLI · gh pr list", action_id="a")
    spinner.set_active_action("Execute · true", action_id="b")
    assert spinner.active_action.startswith("GitHub CLI")

    spinner.clear_active_action("a")
    assert spinner.active_action.startswith("Execute")
    spinner.clear_active_action("b")
    assert spinner.active_action == ""


def test_status_row_with_tool_clips_wide_glyphs_to_one_column_budget() -> None:
    """CJK / emoji must be measured in terminal columns, not code points."""
    from surfaces.shared.terminal.prompt_layout import prompt_line_width, prompt_text_width

    spinner = SpinnerState()
    spinner.start()
    spinner.set_phase(SpinnerState.INVOKING_TOOLS_PHASE)
    spinner.set_active_action("查询 · " + "中" * 200)
    rendered = re.sub(r"\x1b\[[0-9;]*m", "", spinner.inline_spinner_ansi())
    assert prompt_text_width(rendered) <= prompt_line_width()


def test_untracked_tool_end_pops_only_an_untracked_slot() -> None:
    """Regression: an id-less tool_end popped the oldest slot (del[0]) and could
    wipe a still-running named action. It must pop only an id-less slot."""
    spinner = SpinnerState()
    spinner.start()
    spinner.set_active_action("Execute · true", action_id="b")  # named, still running

    spinner.clear_active_action("")  # an untracked tool_end (no id)
    assert spinner.active_action.startswith("Execute")  # named action survives

    # An id-less action is still cleared by an id-less end.
    spinner.set_active_action("GitHub CLI · gh pr list")  # no id
    spinner.clear_active_action("")
    assert spinner.active_action.startswith("Execute")  # popped the id-less one, kept "b"

    spinner.clear_active_action("b")
    assert spinner.active_action == ""


def test_idle_hint_ready_line() -> None:
    rendered = re.sub(r"\x1b\[[0-9;]*m", "", SpinnerState().idle_hint_ansi())
    assert rendered.startswith("Ready")
    assert "/ for commands" in rendered


def test_ready_hint_ansi_matches_spinner_idle_hint() -> None:
    from surfaces.interactive_shell.runtime.core.state import ready_hint_ansi

    assert ready_hint_ansi() == SpinnerState().idle_hint_ansi()


def test_ready_hint_clips_to_one_prompt_column_budget(monkeypatch) -> None:
    from surfaces.interactive_shell.runtime.core.state import ready_hint_ansi
    from surfaces.shared.terminal.prompt_layout import prompt_text_width

    monkeypatch.setattr(
        "surfaces.interactive_shell.runtime.core.state.prompt_line_width",
        lambda: 24,
    )
    rendered = re.sub(r"\x1b\[[0-9;]*m", "", ready_hint_ansi())
    assert prompt_text_width(rendered) <= 24
    assert rendered.startswith("Ready")


def test_status_sentence_shimmers_with_elapsed_time() -> None:
    """Live status text carries a traveling light wave (still one prompt row)."""
    from infrastructure.terminal.theme import set_active_theme
    from surfaces.shared.terminal.prompt_layout import prompt_line_width, prompt_text_width

    set_active_theme("solarized")
    spinner = SpinnerState()
    spinner.start()
    spinner.set_phase(SpinnerState.THINKING_PHASE)
    early = spinner.inline_spinner_ansi()
    spinner.started_at = time.monotonic() - 0.8
    later = spinner.inline_spinner_ansi()
    assert early.count("\x1b[38;2;") >= 5
    assert early != later
    plain = re.sub(r"\x1b\[[0-9;]*m", "", later)
    assert "Thinking…" in plain
    assert prompt_text_width(plain) <= prompt_line_width()


def test_status_shimmer_includes_live_tool_on_same_row() -> None:
    spinner = SpinnerState()
    spinner.start()
    spinner.set_phase(SpinnerState.INVOKING_TOOLS_PHASE)
    spinner.set_active_action("GitHub CLI · gh api repos/x")
    plain = re.sub(r"\x1b\[[0-9;]*m", "", spinner.inline_spinner_ansi())
    assert "Invoking tools…" in plain
    assert "GitHub CLI" in plain
    assert plain.count("\n") == 0
