"""Prompt text, hint, placeholder, and submitted-turn rendering."""

from __future__ import annotations

from prompt_toolkit.formatted_text import ANSI, FormattedText
from rich.console import Console
from rich.text import Text

from core.agent_harness.spi.handoff import parse_ask_user_answers
from infrastructure.terminal import theme as ui_theme
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui.handoff_questions import (
    render_ask_user_qa,
)
from surfaces.interactive_shell.ui.input_prompt.completion import completion_preview_hint_ansi
from surfaces.interactive_shell.ui.input_prompt.layout import (
    _short_meta,
    clip_prompt_text,
)
from surfaces.shared.terminal.prompt_layout import prompt_text_width, terminal_columns

DEFAULT_PLACEHOLDER_TEXT = "Ask about an alert"
_PLAN_CONTINUE_PLACEHOLDER = "continue the plan, or type a message"
#: Warm vertical bar — same role as Droid's orange user-turn lead-in.
_USER_TURN_ACCENT = "▌"


def _placeholder_formatted(text: str) -> FormattedText:
    """Ghost text on the composer plate (style carries INPUT_SURFACE bg)."""
    return FormattedText([("class:placeholder", text)])


def _prompt_turn_number(session: Session) -> int:
    """1-based number for the prompt line currently being entered.

    Derived from the count of accepted submissions, never from
    ``session.history``: one request can append many history rows (shell
    commands, tool executions) but must advance the ``[N]`` label only once.
    """
    return session.terminal.submitted_turn_count + 1


def _counter_text(turn_number: int) -> str:
    return f"[{turn_number}] "


def _prompt_counter_text(session: Session) -> str:
    return _counter_text(_prompt_turn_number(session))


def _prompt_line_ansi(session: Session) -> ANSI:
    del session
    return ANSI(f" {ui_theme.PROMPT_ACCENT_ANSI}>{ui_theme.ANSI_RESET} ")


def _prompt_message(session: Session) -> ANSI:
    """Return the cursor line rendered inside the composer frame."""
    return _prompt_line_ansi(session)


def render_submitted_prompt(console: Console, session: Session, text: str) -> None:
    """Render the submitted user turn above the streamed assistant response.

    Claims the turn's ``[N]`` number: every accepted submission (interactive or
    startup replay) passes through here exactly once, so the counter advances
    once per prompt line regardless of what the turn later records in history.

    Autosubmitted lines (e.g. ``/goal set`` queuing the condition) get a dim
    ``↗ /goal`` marker so the work turn is visually distinct from the slash
    that attached the goal.
    """
    stripped = text.strip()
    # Internal exclusive-stdin turn — never echo ``/choose``. Clear the autosubmit
    # flag the queued ``/choose`` carried so a genuine turn after a cancelled menu
    # reads as a new workload (which resets the ask-user round counter).
    if stripped == "/choose" or stripped.startswith("/choose "):
        session.terminal.last_input_autosubmitted = False
        return
    # A turn is an answer to a hand-off only when a structured picker/Ask-User
    # actually issued one (the harness sets this flag). Do not infer it from the
    # assistant's prose ending in ``?`` — a plain opener like "How can I help?"
    # would then paint every ordinary follow-up as a brand-coloured answer.
    is_handoff_answer = bool(session.terminal.awaiting_handoff_answer)
    session.terminal.awaiting_handoff_answer = False
    ask_user_pairs = parse_ask_user_answers(stripped) if is_handoff_answer else []
    if len(ask_user_pairs) >= 2:
        # Keep the Ask User block in the transcript (Q white, A brand). Claim the
        # turn number so the next prompt still advances; do not paint a fake
        # ``[N] ❯`` — leave this as the Ask User card.
        session.terminal.claim_turn_number()
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
    if autosubmitted:
        # Keep this shorter than the condition — the ``[N] ❯`` line carries the
        # full text; this only answers "is this still /goal set or real work?".
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
    counter = _counter_text(session.terminal.claim_turn_number())
    lines = text.splitlines() or [""]
    # Full-width surface plate + warm left bar (Droid paints the user row
    # edge-to-edge). Write palette ANSI directly — Rich Text/Style.parse on a
    # _LazyRichStyle can fall through to default white under coverage.
    # Full terminal width plate (Droid edge-to-edge). Trailing pad spaces stay
    # inside the surface so the last column is never a glyph (soft-wrap safe).
    row_width = max(terminal_columns(), 1)
    accent_ansi = ui_theme.BOLD_REPLY_MARKER_ANSI
    body_ansi = ui_theme.BRAND_ANSI if is_handoff_answer else ui_theme.TEXT_ANSI
    counter_ansi = ui_theme.DIM_ANSI
    surface = ui_theme.INPUT_SURFACE_BG_ANSI
    parts: list[str] = []
    for index, line in enumerate(lines):
        if index:
            parts.append("\n")
        if index == 0:
            # ``▌ [N] `` then body — bar sits on the left edge of the plate.
            prefix = f"{_USER_TURN_ACCENT} {counter}"
            prefix_cols = prompt_text_width(prefix)
            body = clip_prompt_text(line, max(1, row_width - prefix_cols))
            pad = max(0, row_width - prefix_cols - prompt_text_width(body))
            parts.append(
                f"{surface}{accent_ansi}{_USER_TURN_ACCENT}{ui_theme.ANSI_RESET}"
                f"{surface} {counter_ansi}{counter}{ui_theme.ANSI_RESET}"
                f"{surface}{body_ansi}{body}{' ' * pad}{ui_theme.ANSI_RESET}"
            )
        else:
            # Hang under the accent + space so wrapped lines stay in the plate.
            hang = "  " + (" " * len(counter))
            hang_cols = prompt_text_width(hang)
            body = clip_prompt_text(line, max(1, row_width - hang_cols))
            pad = max(0, row_width - hang_cols - prompt_text_width(body))
            parts.append(
                f"{surface}{counter_ansi}{hang}{ui_theme.ANSI_RESET}"
                f"{surface}{body_ansi}{body}{' ' * pad}{ui_theme.ANSI_RESET}"
            )
    # Single trailing newline — the reply path owns the blank row under the
    # user plate so we do not stack two spacers (Droid: one row of margin).
    console.file.write("".join(parts) + "\n")
    console.file.flush()


def resolve_prompt_prefix_ansi(*, inline_spinner: str, idle_hint: str) -> str:
    """Choose the prompt's top context line: spinner, completion preview, or idle hint."""
    if inline_spinner:
        return inline_spinner
    preview = completion_preview_hint_ansi()
    return preview or idle_hint


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
