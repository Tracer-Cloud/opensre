"""Prompt text, hint, placeholder, and submitted-turn rendering."""

from __future__ import annotations

from prompt_toolkit.formatted_text import ANSI, FormattedText
from rich.console import Console
from rich.text import Text

from core.agent_harness.spi.handoff import parse_ask_user_answers
from core.agent_harness.spi.session_goal import session_goal_is_active
from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui.handoff_questions import (
    render_ask_user_qa,
)
from surfaces.interactive_shell.ui.input_prompt.layout import _short_meta
from surfaces.interactive_shell.ui.transcript import (
    TranscriptRole,
    is_internal_turn,
    user_turn_renderable,
)
from surfaces.shared.terminal.components.rendering import print_repl_renderable

DEFAULT_PLACEHOLDER_TEXT = "Drop a repo link. Watch it find your CI waste."
_PLAN_CONTINUE_PLACEHOLDER = "continue the plan, or type a message"


def _placeholder_formatted(text: str) -> FormattedText:
    """Ghost text on the composer plate (style carries INPUT_SURFACE bg)."""
    return FormattedText([("class:placeholder", text)])


def _prompt_line_ansi(session: Session) -> ANSI:
    del session
    return ANSI(f" {ui_theme.PROMPT_ACCENT_ANSI}{TranscriptRole.USER.value}{ui_theme.ANSI_RESET} ")


def _prompt_message(session: Session) -> ANSI:
    """Return the cursor line rendered inside the composer frame."""
    return _prompt_line_ansi(session)


def render_submitted_prompt(console: Console, session: Session, text: str) -> None:
    """Render the submitted user turn above the streamed assistant response.

    Shares :func:`user_turn_renderable` with the ``/resume`` replay so a
    restored turn is indistinguishable from a live one, and routes through
    ``print_repl_renderable`` so the full-screen transcript lays the row out
    again at every width instead of keeping it as fixed-width bytes.

    Autosubmitted lines (e.g. ``/goal set`` queuing the condition) get a dim
    ``↗ /goal`` marker so the work turn is visually distinct from the slash
    that attached the goal.
    """
    stripped = text.strip()
    # Internal exclusive-stdin turn — never echo it. Clear the autosubmit flag the
    # queued ``/choose`` carried so a genuine turn after a cancelled menu reads as
    # a new workload (which resets the ask-user round counter).
    if is_internal_turn(stripped):
        session.terminal.last_input_autosubmitted = False
        return
    # A turn is an answer to a hand-off only when a structured picker/Ask-User
    # actually issued one (the harness sets this flag). Do not infer it from the
    # assistant's prose ending in ``?`` — a plain opener like "How can I help?"
    # would then paint every ordinary follow-up as a brand-coloured answer.
    is_handoff_answer = bool(session.terminal.awaiting_handoff_answer)
    session.terminal.awaiting_handoff_answer = False
    recap_painted = session.terminal.handoff_recap_text == stripped
    session.terminal.handoff_recap_text = None
    ask_user_pairs = parse_ask_user_answers(stripped) if is_handoff_answer else []
    if len(ask_user_pairs) >= 2 and not recap_painted:
        # Keep the Ask User block in the transcript (Q white, A brand) rather
        # than repainting the raw answer text as an ordinary user row.
        render_ask_user_qa(console, ask_user_pairs)
        return
    autosubmitted = bool(session.terminal.last_input_autosubmitted)
    session.terminal.last_input_autosubmitted = False
    if is_handoff_answer and autosubmitted:
        # A fixed picker choice already has a compact persistent result. Do not
        # manufacture a second user turn in scrollback; only mark the synthetic
        # answer (the label alone, not the question it travels with) so a no-op
        # model acknowledgement can be omitted as well.
        session.terminal.pending_choice_response = (
            ask_user_pairs[0][1] if len(ask_user_pairs) == 1 else stripped
        )
        return
    if autosubmitted and session_goal_is_active(session):
        # Keep this shorter than the condition — the user row carries the full
        # text; this only answers "is this still /goal set or real work?".
        # Other autosubmits (a queued picker, a demo prompt) get the plain row.
        console.print()
        console.print(
            Text(
                "↗ /goal — work turn (condition auto-submitted)",
                style=str(ui_theme.DIM),
            )
        )
    else:
        # Blank row between the previous turn and this one (Droid rhythm).
        console.print()
    print_repl_renderable(
        console,
        user_turn_renderable(
            text,
            marker_style=str(ui_theme.HIGHLIGHT),
            body_style=str(ui_theme.BRAND if is_handoff_answer else ui_theme.TEXT),
            background=f"on {ui_theme.INPUT_SURFACE}",
        ),
    )


def resolve_prompt_prefix_ansi(*, inline_spinner: str, idle_hint: str) -> str:
    """Keep runtime status above the composer; completion details belong in its tray."""
    if inline_spinner:
        return inline_spinner
    return idle_hint


def resolve_idle_hint_ansi(session: Session) -> str:
    """No idle chrome above the composer.

    The command/shortcut hints live once in the launch banner and the composer
    footer, so the prompt does not repeat a "Ready · …" line on every turn. That
    recurring line also stacked into duplicate copies on terminal resize; with
    nothing rendered here, there is nothing to leave behind.
    """
    del session
    return ""


def ctrl_c_exit_hint_ansi() -> str:
    """Return the transient double-press exit hint for the fixed status row."""
    return f"{ui_theme.DIM_ANSI}(Press Ctrl+C again to exit){ui_theme.ANSI_RESET}"


def composer_footer_ansi() -> str:
    """No footer row. The empty box is the job prompt; shortcuts live on ``?``."""
    return ""


def resolve_prompt_placeholder(session: Session) -> FormattedText:
    """Contextual ghost text when the input buffer is empty.

    Built per redraw (not at import) so theme styles cannot freeze stale, and so
    an unfinished live plan can replace the default exploratory hint. Uses a
    style class (not raw ANSI) so the composer INPUT_SURFACE fill is preserved.
    """
    parts: list[str] = []
    if session.terminal.trust_mode:
        parts.append("trust on")
    running = session.task_registry.running_count()
    if running:
        parts.append(f"{running} task{'s' if running != 1 else ''} running")
    if session.resumed_from_name:
        parts.append(f"resumed: {_short_meta(session.resumed_from_name, max_len=32)}")
    if parts:
        return _placeholder_formatted(" · ".join(parts))
    if (
        session.task_plan is not None
        and session.task_plan.all_pending
        and session.plan_only_until_authorized
    ):
        return _placeholder_formatted("say go to start the plan, or type a message")
    plan = session.task_plan
    if (
        plan is not None
        and plan.steps
        and not plan.all_completed
        and not session.plan_only_until_authorized
    ):
        return _placeholder_formatted(_PLAN_CONTINUE_PLACEHOLDER)
    return _placeholder_formatted(DEFAULT_PLACEHOLDER_TEXT)
