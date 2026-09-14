"""Single entry point composing the full terminal UI render.

The terminal UI has three pieces, all composed from this module:

1. compact launch banner (wordmark + install health)
2. Thinking / Invoking (when busy) plus the Auto permission line
3. bordered ``>`` composer (job-shaped placeholder; no help footer)

Piece 1 is static chrome printed once by :func:`render_terminal_ui`.
Pieces 2–3 form the live prompt region: prompt-toolkit re-evaluates them on
every keystroke, spinner tick, and prompt invalidation, so they are composed
by :func:`render_prompt_region`, which ``PromptBuilder`` calls per redraw.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prompt_toolkit.formatted_text import ANSI
from rich.console import Console

from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.ui.ci_fix_status import prompt_status_ansi
from surfaces.interactive_shell.ui.hooks import confirmation_choice_overlay_ansi
from surfaces.interactive_shell.ui.input_prompt import rendering as prompt_rendering
from surfaces.interactive_shell.ui.prompt_visibility import (
    hidden_typing_box_pad,
    typing_box_hidden,
)
from surfaces.interactive_shell.ui.task_plan import task_plan_overlay_ansi
from surfaces.shared.terminal.banner import render_launch_banner
from surfaces.shared.terminal.components.cpr_stdin import strip_cpr_sequences
from surfaces.shared.terminal.prompt_layout import clip_prompt_text, prompt_line_width

if TYPE_CHECKING:
    from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
    from surfaces.interactive_shell.session import Session


def render_terminal_ui(
    console: Console | None = None,
    *,
    session: object = None,
    animate: bool = True,
) -> None:
    """Render the static terminal chrome: the compact launch banner.

    ``animate=False`` prints the banner without the startup spin — used when
    the spin already ran on its own thread while the runtime booted.
    """
    console = console or Console(
        highlight=False,
        force_terminal=True,
        color_system="truecolor",
        legacy_windows=False,
    )
    render_launch_banner(console, session=session, animate=animate)


def render_prompt_region(session: Session, state: ReplState, spinner: SpinnerState) -> ANSI:
    """Compose the live prompt region: context line plus rule and input prefix.

    The top line is the pending confirmation prompt when one is active,
    otherwise Thinking / Invoking while a turn is running, then the ``/auto``
    permission line. A rendered task plan has one blank row beneath it before
    this status chrome.

    When confirmation or exclusive-stdin structured input owns the keyboard,
    the free-text typing box (rule + ``[N] ❯``) is omitted so it does not
    compete with Ask User / option menus — free text is itself an option
    (``Or type your own answer...``), not a parallel composer.

    The stream already prints one blank after a *finished* reply. Mid-turn
    Thinking sits under still-streaming text with no that margin, so the busy
    path leads with one blank row. Idle still has no empty "Ready" placeholder.
    """
    if typing_box_hidden(session, state):
        # Same newline count as ``_prompt_message`` so confirmation does not
        # shift the live region height under ``patch_stdout``.
        base = hidden_typing_box_pad()
    else:
        base = prompt_rendering._prompt_message(session).value
    plan = session.task_plan
    if plan is None or not plan.steps or _plan_already_in_transcript(session, plan, state):
        # Drop expand so the next plan opens collapsed rather than inheriting
        # a sticky Ctrl+P from a previous checklist.
        state.plan_expanded = False
        state.plan_step_texts = None
        plan_overlay = ""
    else:
        # Status-only updates keep expand; a different checklist must not.
        step_texts = tuple(item.step for item in plan.steps)
        if state.plan_step_texts is not None and state.plan_step_texts != step_texts:
            state.plan_expanded = False
        state.plan_step_texts = step_texts
        plan_overlay = strip_cpr_sequences(
            task_plan_overlay_ansi(plan, expanded=state.plan_expanded)
        )
    # Droid block rhythm: blank row above the checklist (separates scrollback
    # notes from the pinned plan) and one blank beneath before status chrome.
    plan_prefix = f"\n{plan_overlay}\n\n" if plan_overlay else ""

    # A pending confirmation renders a stacked, arrow-navigable Yes/No choice
    # (box hidden). Density matches the streaming stack: status → Auto → composer.
    if state.is_awaiting_confirmation():
        auto_line = strip_cpr_sequences(prompt_status_ansi(session, quiet=False))
        choice = _confirmation_block(state)
        return ANSI(f"{plan_prefix}{choice}\n{auto_line}\n{base}")

    if state.is_ctrl_c_exit_hint_visible():
        prefix = prompt_rendering.ctrl_c_exit_hint_ansi()
        inline_spinner = ""
    else:
        inline_spinner = spinner.inline_spinner_ansi()
        prefix = strip_cpr_sequences(
            prompt_rendering.resolve_prompt_prefix_ansi(
                inline_spinner=inline_spinner,
                idle_hint=prompt_rendering.resolve_idle_hint_ansi(session),
            )
        )
    # Tools already paint a labeled row into scrollback; the live tool name is
    # folded into the spinner status row (same line as ``Invoking tools…``).
    # Auto stays on the page while busy (DIM) so permission chrome does not
    # vanish for the length of the turn.
    auto_line = strip_cpr_sequences(prompt_status_ansi(session, quiet=bool(inline_spinner)))
    # Mid-turn stream text has no trailing blank (that lands only when the
    # reply finishes). One lead row under Thinking/Invoking so status chrome
    # does not sit flush on the last assistant line. Skip when a plan overlay
    # already supplies the gap, and skip when idle (no status prefix).
    status_lead = "\n" if prefix and not plan_prefix else ""
    if prefix:
        return ANSI(f"{plan_prefix}{status_lead}{prefix}\n{auto_line}\n{base}")
    return ANSI(f"{plan_prefix}{auto_line}\n{base}")


_CONFIRM_HINT = "↑↓ Navigate • Enter confirm • Esc cancel"


def _plan_already_in_transcript(session: Session, plan: Any, state: ReplState) -> bool:
    """True once a finished plan's breakdown is in scrollback and no turn is running.

    The one-shot ``Plan complete`` breakdown is the durable record; keeping the
    pinned copy as well showed the same checklist twice.
    """
    return bool(
        plan.all_completed
        and getattr(session, "task_plan_breakdown_emitted", False)
        and not state.is_dispatch_running()
    )


def _confirmation_block(state: ReplState) -> str:
    """Header, the stacked ``[a] Yes`` / ``[b] No`` choice, and a nav hint.

    Dense like the streaming stack: header, choice, and hint on consecutive
    lines so confirm doesn't feel taller than Thinking/Invoking.
    """
    header = clip_prompt_text(state.confirm_prompt_text.strip(), prompt_line_width())
    header_ansi = f"{ui_theme.SECONDARY_ANSI}{header}{ui_theme.ANSI_RESET}"
    choice = strip_cpr_sequences(
        confirmation_choice_overlay_ansi(state.confirm_selected, state.confirm_options)
    )
    hint = f"{ui_theme.DIM_ANSI}{_CONFIRM_HINT}{ui_theme.ANSI_RESET}"
    return f"{header_ansi}\n{choice}\n{hint}"


__all__ = ["render_prompt_region", "render_terminal_ui"]
