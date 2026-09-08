"""Slash command: open the pending interactive selection menu (``/choose``).

The ``ask_user_choice`` action tool stores a
:class:`~core.agent_harness.session.pending_choice.PendingUserChoice` on the
session and queues this command via ``set_auto_command``, so it runs as a
literal slash turn with exclusive stdin — the only place a raw-stdin arrow-key
picker is safe (see ``_EXCLUSIVE_STDIN_MENU_COMMANDS`` in
``runtime/input_policy.py``). The selected option label is auto-submitted
as the next user message so the agent receives the decision verbatim.
"""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from core.agent_harness.spi.handoff import format_ask_user_answers
from infrastructure.terminal import theme as ui_theme
from infrastructure.terminal.notify import NotifyEvent, play_notification
from surfaces.interactive_shell.command_registry.types import SlashCommand
from surfaces.interactive_shell.runtime import Session
from surfaces.interactive_shell.ui.ask_user import CUSTOM_OPTION, repl_ask_user
from surfaces.interactive_shell.ui.handoff_questions import render_choice_selection
from surfaces.interactive_shell.ui.prompt_visibility import clear_live_prompt_paint
from surfaces.shared.terminal.components.choice_menu import (
    print_valid_choice_list,
    repl_choose_one,
    repl_tty_interactive,
)


def _cmd_choose(session: Session, console: Console, args: list[str]) -> bool:
    del args
    pending = session.pending_user_choice
    session.pending_user_choice = None
    if pending is None:
        console.print(f"[{ui_theme.DIM}]No selection menu is pending.[/]")
        return True

    if not repl_tty_interactive():
        for question in pending.items():
            print_valid_choice_list(
                console,
                title=question.title,
                choices=list(question.options),
            )
        console.print(f"[{ui_theme.DIM}]Reply with the option you want.[/]")
        return True

    items = pending.items()
    clear_live_prompt_paint(session)
    play_notification(NotifyEvent.INPUT_NEEDED)  # the agent is now waiting on the user
    if pending.is_batch():
        picked = repl_ask_user(items)
        if picked is None:
            console.print(f"[{ui_theme.DIM}]Selection cancelled — type a reply instead.[/]")
            session.terminal.awaiting_handoff_answer = False
            return True
        session.terminal.set_auto_command(format_ask_user_answers(items, picked))
        session.terminal.awaiting_handoff_answer = True
        return True

    option_choices = [(option, option) for option in items[0].options]
    option_choices.append((CUSTOM_OPTION, CUSTOM_OPTION))
    # Custom row: type in place on the OpenSRE option array (Droid-style).
    picked_one = repl_choose_one(
        title=items[0].title,
        choices=option_choices,
        custom_label=CUSTOM_OPTION,
        multi_select=items[0].multi_select,
        header="Ask User",
        letter_keys=True,
    )
    if picked_one is None:
        console.print(f"[{ui_theme.DIM}]Selection cancelled — type a reply instead.[/]")
        session.terminal.awaiting_handoff_answer = False
        return True

    command = pending.commands.get(picked_one) or (picked_one if picked_one.startswith("/") else "")
    if command:
        # A mapped option, or a slash command typed into the custom row, is a
        # command the shell runs, not an answer for the model.
        console.print(f"[{ui_theme.DIM}]Running {escape(command)}.[/]")
        session.terminal.awaiting_handoff_answer = False
        session.terminal.set_auto_command(command)
        return True
    render_choice_selection(console, items[0].title, picked_one)
    # The answer travels with its question, as the batched wizard's does: a bare
    # label such as "owner/repo (757 commits, CI configured)" reads to the
    # planner like a fresh request and gets re-asked or re-routed.
    session.terminal.set_auto_command(format_ask_user_answers(items, (picked_one,)))
    session.terminal.awaiting_handoff_answer = True
    return True


COMMANDS: list[SlashCommand] = [
    SlashCommand(
        "/choose",
        "Open the pending interactive selection menu queued by the agent.",
        _cmd_choose,
        usage=("/choose",),
        # Renders the queued read-only picker; never mutates anything.
        mutating=False,
    )
]

__all__ = ["COMMANDS"]
