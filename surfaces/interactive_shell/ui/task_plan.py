"""Live task-plan overlay and themed post-execution breakdown for the shell.

The live plan renders as an ANSI overlay pinned above the prompt. Before
execution the whole checklist is shown; once work starts it collapses to the
header plus the current step so the prompt region stays short while tool
output streams above it. The live checklist is never dumped into the
transcript — only the one-shot ``Plan complete`` breakdown is.
"""

from __future__ import annotations

from rich.console import Console, Group
from rich.text import Text

from core.agent_harness.spi.task_plan import (
    PLAN_STATUS_GLYPH,
    PlanStep,
    PlanStepStatus,
    TaskPlan,
    format_plan_header,
    parse_task_plan,
)
from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.ui.input_prompt.layout import clip_prompt_text, prompt_line_width
from surfaces.shared.terminal.components.rendering import print_repl_renderable

_STEP_INDENT = "  "
# Steps shown before the plan collapses; a longer plan folds to a window
# around the current step until the user expands it.
_COLLAPSED_MAX_STEPS = 3
_EXPAND_HINT = "Ctrl+P to view all"
# Work-note lines in the post-execution breakdown (``↳ …`` under each step).
_WORK_NOTE_MARKER = "↳"


def task_plan_from_tool_args(args: dict[str, object]) -> TaskPlan | None:
    """Parse a plan from an ``update_plan`` tool-call payload."""
    if not isinstance(args, dict):
        return None
    plan, _error = parse_task_plan(args)
    return plan


def render_plan_breakdown(console: Console, breakdown: str) -> None:
    """Paint the one-shot plan breakdown with Droid/Cursor/Claude hierarchy.

    Checked steps stay primary (warm ``✓`` + body text); nested work notes
    under ``↳`` go dim so the checklist reads apart from tool chatter —
    the same parent/child split live tool rows already use. The rows go out
    as one buffered block so they stay left-aligned when the prompt has the
    terminal in raw mode.
    """
    text = (breakdown or "").rstrip("\n")
    if not text:
        return
    rows: list[Text] = []
    for raw in text.splitlines():
        stripped = raw.lstrip()
        if not stripped:
            rows.append(Text(""))
            continue
        if stripped.startswith(_WORK_NOTE_MARKER):
            # Theme DIM as raw truecolor. Rich Style.parse caches ANSI from
            # the first color_system that rendered ``#6E6E6E``; a prior
            # 16-color console then emits bright-black ``[90m`` instead of
            # DIM_ANSI on a truecolor breakdown.
            rows.append(Text.from_ansi(f"{ui_theme.DIM_ANSI}{raw}{ui_theme.ANSI_RESET}"))
            continue
        line = Text()
        # Header (``Plan complete · n/n``) or a checklist step (``  ✓ …``).
        if stripped.startswith("Plan"):
            line.append(raw, style=str(ui_theme.SECONDARY))
            rows.append(line)
            continue
        # Step row: accent the status glyph, keep the step title as body text.
        indent_len = len(raw) - len(stripped)
        if indent_len:
            line.append(raw[:indent_len])
        glyph, _, rest = stripped.partition(" ")
        if glyph in PLAN_STATUS_GLYPH.values() and rest:
            glyph_style = (
                str(ui_theme.HIGHLIGHT)
                if glyph == PLAN_STATUS_GLYPH[PlanStepStatus.COMPLETED]
                else str(ui_theme.TEXT)
            )
            line.append(glyph, style=glyph_style)
            line.append(" ")
            line.append(rest, style=str(ui_theme.TEXT))
        else:
            line.append(stripped, style=str(ui_theme.TEXT))
        rows.append(line)
    print_repl_renderable(console, Group(*rows))


def _overlay_line(text: str, style: str, width: int) -> str:
    """One ANSI overlay row: strip controls via ``clip_prompt_text``, then style.

    ``clip_prompt_text`` is the single sanitize+truncate boundary for raw ANSI
    overlays (including ``PlanStep`` instances built outside ``parse_task_plan``).
    """
    return f"{style}{clip_prompt_text(text, width)}{ui_theme.ANSI_RESET}"


def _step_overlay_line(item: PlanStep, width: int) -> str:
    step = item.step
    glyph = PLAN_STATUS_GLYPH[item.status]
    if item.status is PlanStepStatus.IN_PROGRESS:
        return _overlay_line(
            f"{glyph} {step}",
            f"{ui_theme.ANSI_BOLD}{ui_theme.TEXT_ANSI}",
            width,
        )
    if item.status is PlanStepStatus.COMPLETED:
        clipped = clip_prompt_text(step, max(width - 2, 1))
        return (
            f"{ui_theme.HIGHLIGHT_ANSI}{glyph} {ui_theme.ANSI_RESET}"
            f"{ui_theme.DIM_ANSI}{clipped}{ui_theme.ANSI_RESET}"
        )
    return _overlay_line(f"{glyph} {step}", ui_theme.DIM_ANSI, width)


def _indented_step_overlay_line(item: PlanStep, width: int) -> str:
    """A step overlay row indented under the header."""
    return _STEP_INDENT + _step_overlay_line(item, max(width - len(_STEP_INDENT), 1))


def _focus_index(plan: TaskPlan) -> int:
    """Index the collapsed window centers on: the current step (else next up)."""
    for index, item in enumerate(plan.steps):
        if item.status is PlanStepStatus.IN_PROGRESS:
            return index
    for index, item in enumerate(plan.steps):
        if item.status is PlanStepStatus.PENDING:
            return index
    return len(plan.steps) - 1


def _collapsed_window(plan: TaskPlan) -> tuple[int, int]:
    """``[start, end)`` slice of steps to show when the plan is collapsed."""
    total = len(plan.steps)
    half = _COLLAPSED_MAX_STEPS // 2
    start = max(0, min(_focus_index(plan) - half, total - _COLLAPSED_MAX_STEPS))
    return start, start + _COLLAPSED_MAX_STEPS


def task_plan_overlay_ansi(plan: TaskPlan, *, expanded: bool = False) -> str:
    """ANSI plan overlay pinned above the prompt (Droid checklist rhythm).

    Header flush left; steps indented two spaces under it (``✓`` / ``●`` /
    ``○``). Same left edge as note ``·`` / Thinking glyphs in column 0; step
    glyphs sit under note body text. A short plan (or ``expanded``) shows every
    step; a longer one collapses to a window around the current step.
    """
    width = prompt_line_width()
    header = _overlay_line(format_plan_header(plan), ui_theme.SECONDARY_ANSI, width)
    total = len(plan.steps)
    if expanded or total <= _COLLAPSED_MAX_STEPS:
        rows = [header, *(_indented_step_overlay_line(item, width) for item in plan.steps)]
        return "\n".join(rows)

    start, end = _collapsed_window(plan)
    rows = [header]
    if start > 0:
        rows.append(_overlay_line(f"{_STEP_INDENT}… {start} earlier", ui_theme.DIM_ANSI, width))
    rows.extend(_indented_step_overlay_line(item, width) for item in plan.steps[start:end])
    hidden_after = total - end
    tail = f"… {hidden_after} more · {_EXPAND_HINT}" if hidden_after else _EXPAND_HINT
    rows.append(_overlay_line(f"{_STEP_INDENT}{tail}", ui_theme.DIM_ANSI, width))
    return "\n".join(rows)


__all__ = [
    "render_plan_breakdown",
    "task_plan_from_tool_args",
    "task_plan_overlay_ansi",
]
