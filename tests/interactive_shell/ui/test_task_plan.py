"""Task-plan overlay rendering (the plan lives only in the pinned overlay)."""

from __future__ import annotations

import re

from core.agent_harness.task_plan.plan import parse_task_plan
from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.task_plan import task_plan_overlay_ansi
from surfaces.interactive_shell.ui.terminal_ui import render_prompt_region


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _sample_plan():
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Capture 502 samples from checkout", "status": "completed"},
                {"step": "Trace 502s to the last deploy", "status": "in_progress"},
                {"step": "Confirm checkout returns 2xx", "status": "pending"},
            ]
        }
    )
    assert error is None and plan is not None
    return plan


def test_all_pending_overlay_shows_the_full_indented_checklist() -> None:
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Confirm scope", "status": "pending"},
                {"step": "Verify recovery", "status": "pending"},
            ]
        }
    )
    assert error is None and plan is not None
    overlay = _strip_ansi(task_plan_overlay_ansi(plan))
    lines = overlay.splitlines()
    assert lines[0] == "Plan ready · 0/2 executed"
    # Every step shown, indented under the header.
    assert lines[1] == "  ○ Confirm scope"
    assert lines[2] == "  ○ Verify recovery"


def test_overlay_shows_the_full_checklist_during_execution() -> None:
    # Every step stays visible with its progress glyph while work runs.
    overlay = _strip_ansi(task_plan_overlay_ansi(_sample_plan()))
    lines = overlay.splitlines()
    assert lines[0].startswith("Plan · 2/3")
    assert lines[1] == "  ✓ Capture 502 samples from checkout"
    assert lines[2] == "  ● Trace 502s to the last deploy"
    assert lines[3] == "  ○ Confirm checkout returns 2xx"


def test_overlay_strips_control_characters_from_a_raw_step() -> None:
    from core.agent_harness.task_plan.plan import PlanStep, PlanStepStatus, TaskPlan

    plan = TaskPlan(
        steps=(
            PlanStep(step="\x1b]0;pwn\x07Capture samples", status=PlanStepStatus.IN_PROGRESS),
            PlanStep(step="Verify recovery", status=PlanStepStatus.PENDING),
        )
    )
    overlay = task_plan_overlay_ansi(plan)
    assert "\x1b]" not in overlay
    assert "\x07" not in overlay
    assert "Capture samples" in overlay


def test_clip_text_strips_controls_before_measuring_width() -> None:
    from surfaces.interactive_shell.ui.input_prompt.layout import clip_prompt_text

    assert clip_prompt_text("\x1b" * 50 + "ok", 5) == "ok"
    clipped = clip_prompt_text("\x1b]0;pwn\x07hello", 80)
    assert "\x1b" not in clipped
    assert "\x07" not in clipped
    assert "hello" in clipped


def test_prompt_region_keeps_the_checklist_above_invoking_tools() -> None:
    session = Session()
    session.task_plan = _sample_plan()
    spinner = SpinnerState()
    spinner.start()
    spinner.set_phase(SpinnerState.INVOKING_TOOLS_PHASE)
    rendered = _strip_ansi(render_prompt_region(session, ReplState(), spinner).value)
    assert "Plan · 2/3" in rendered
    assert "● Trace 502s to the last deploy" in rendered
    assert SpinnerState.INVOKING_TOOLS_PHASE in rendered
    assert rendered.index("Plan · 2/3") < rendered.index(SpinnerState.INVOKING_TOOLS_PHASE)
    # Auto stays on the page (DIM) while busy, below the Invoking status row.
    assert "Auto (High)" in rendered
    assert rendered.index(SpinnerState.INVOKING_TOOLS_PHASE) < rendered.index("Auto (High)")


def test_idle_prompt_region_shows_plan_without_thinking_or_ready_hint() -> None:
    session = Session()
    session.task_plan = _sample_plan()
    rendered = _strip_ansi(render_prompt_region(session, ReplState(), SpinnerState()).value)
    assert "Thinking" not in rendered
    assert SpinnerState.EXECUTING_PHASE not in rendered
    assert "Plan · 2/3" in rendered
    assert "Ready" not in rendered  # no recurring idle hint line
    assert rendered.index("Plan · 2/3") < rendered.index("Auto (High)")
    assert "  ○ Confirm checkout returns 2xx\n\nAuto (High) · Allow all" in rendered
    # Blank row above the plan separates it from scrollback notes (Droid blocks).
    assert rendered.lstrip().startswith("Plan · 2/3") or "\nPlan · 2/3" in rendered


def test_clearing_the_plan_resets_expanded_state() -> None:
    session = Session()
    session.task_plan = _sample_plan()
    state = ReplState()
    state.plan_expanded = True
    _ = render_prompt_region(session, state, SpinnerState())
    assert state.plan_expanded is True

    session.task_plan = None
    _ = render_prompt_region(session, state, SpinnerState())
    assert state.plan_expanded is False


def _long_plan():
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Inspect the repo", "status": "completed"},
                {"step": "Read the config", "status": "completed"},
                {"step": "Patch the bug", "status": "in_progress"},
                {"step": "Run the tests", "status": "pending"},
                {"step": "Confirm green", "status": "pending"},
            ]
        }
    )
    assert error is None and plan is not None
    return plan


def test_long_plan_collapses_to_a_window_around_the_current_step() -> None:
    # A 5-step plan folds to the current step plus one neighbour each side,
    # with count markers for the hidden ranges and a hint to expand.
    overlay = _strip_ansi(task_plan_overlay_ansi(_long_plan()))
    lines = overlay.splitlines()

    assert lines[0].startswith("Plan · 3/5")
    assert lines[1] == "  … 1 earlier"
    assert lines[2] == "  ✓ Read the config"
    assert lines[3] == "  ● Patch the bug"
    assert lines[4] == "  ○ Run the tests"
    assert lines[5] == "  … 1 more · Ctrl+P to view all"
    # The collapsed view hides the far ends.
    assert "Inspect the repo" not in overlay
    assert "Confirm green" not in overlay


def test_expanded_long_plan_shows_every_step() -> None:
    overlay = _strip_ansi(task_plan_overlay_ansi(_long_plan(), expanded=True))
    lines = overlay.splitlines()

    assert lines[0].startswith("Plan · 3/5")
    assert lines[1] == "  ✓ Inspect the repo"
    assert lines[5] == "  ○ Confirm green"
    assert "earlier" not in overlay
    assert "to view all" not in overlay
    assert "Ctrl+P" not in overlay


def _other_long_plan():
    plan, error = parse_task_plan(
        {
            "plan": [
                {"step": "Map the services", "status": "completed"},
                {"step": "Pull the traces", "status": "completed"},
                {"step": "Find the timeout", "status": "in_progress"},
                {"step": "Raise the limit", "status": "pending"},
                {"step": "Verify p99", "status": "pending"},
            ]
        }
    )
    assert error is None and plan is not None
    return plan


def test_replacing_a_plan_resets_expanded_state() -> None:
    # Expanding plan A must not stick when plan B is assigned without a
    # None/empty gap — the replacement should open in its collapsed window.
    session = Session()
    session.task_plan = _long_plan()
    state = ReplState()
    state.plan_expanded = True
    first = _strip_ansi(render_prompt_region(session, state, SpinnerState()).value)
    assert state.plan_expanded is True
    assert "Inspect the repo" in first

    session.task_plan = _other_long_plan()
    second = _strip_ansi(render_prompt_region(session, state, SpinnerState()).value)
    assert state.plan_expanded is False
    assert "Ctrl+P to view all" in second
    assert "Map the services" not in second
    assert "Verify p99" not in second


def test_updating_plan_status_keeps_expanded_state() -> None:
    # Status-only updates are the same checklist; an open overlay stays open.
    session = Session()
    session.task_plan = _long_plan()
    state = ReplState()
    _ = render_prompt_region(session, state, SpinnerState())
    state.plan_expanded = True

    updated, error = parse_task_plan(
        {
            "plan": [
                {"step": "Inspect the repo", "status": "completed"},
                {"step": "Read the config", "status": "completed"},
                {"step": "Patch the bug", "status": "completed"},
                {"step": "Run the tests", "status": "in_progress"},
                {"step": "Confirm green", "status": "pending"},
            ]
        }
    )
    assert error is None and updated is not None
    session.task_plan = updated
    rendered = _strip_ansi(render_prompt_region(session, state, SpinnerState()).value)
    assert state.plan_expanded is True
    assert "Inspect the repo" in rendered
    assert "Confirm green" in rendered


def test_plan_breakdown_dims_work_notes_and_accents_checked_steps() -> None:
    """Droid/Cursor/Claude: checklist steps primary; ``↳`` work notes dim."""
    import io

    from rich.color import ColorSystem
    from rich.console import Console
    from rich.style import Style

    import infrastructure.terminal.theme as ui_theme
    from surfaces.interactive_shell.ui.task_plan import render_plan_breakdown

    ui_theme.set_active_theme("amber")
    # A 16-color render of theme DIM must not pin work notes to bright-black.
    # Rich stores the first color_system on the cached Style; reset it so this
    # poison does not leak to later tests in the same process.
    dim_style = Style.parse(str(ui_theme.DIM))
    dim_style._make_ansi_codes(ColorSystem.STANDARD)
    breakdown = (
        "Plan complete · 2/2\n"
        "  ✓ Confirm repository\n"
        "      ↳ GitHub CLI · gh repo view\n"
        "  ✓ Collect workflow runs\n"
        "      ↳ Python · analyze runs"
    )
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        highlight=False,
        no_color=False,
        width=80,
    )
    try:
        render_plan_breakdown(console, breakdown)
        out = buf.getvalue()
    finally:
        dim_style._ansi = None
        Style.parse.cache_clear()
    plain = _strip_ansi(out)
    assert "Plan complete · 2/2" in plain
    assert "✓ Confirm repository" in plain
    assert "↳ GitHub CLI · gh repo view" in plain
    # Work notes use DIM; checked glyphs use HIGHLIGHT — not one flat color.
    assert ui_theme.DIM_ANSI in out
    assert "\x1b[90m" not in out
    assert ui_theme.HIGHLIGHT_ANSI in out
    assert ui_theme.TEXT_ANSI in out
    # Caption is secondary, distinct from step body and work notes.
    assert ui_theme.SECONDARY_ANSI in out
