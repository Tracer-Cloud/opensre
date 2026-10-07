"""Interactive prompt helpers (Escape to cancel, etc.)."""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import questionary
import questionary.question
from prompt_toolkit.key_binding import KeyBindings, KeyBindingsBase, merge_key_bindings
from prompt_toolkit.keys import Keys
from rich.console import Console

from infrastructure.terminal.theme import DIM, HIGHLIGHT

# Matches the REPL's ❯ prompt (surfaces/interactive_shell/ui/input_prompt) instead of
# questionary's default "?" qmark, so every questionary prompt in the app reads the same.
QUESTIONARY_QMARK = "❯"

_escape_patch_installed: list[bool] = [False]
_ctrl_c_patch_installed: list[bool] = [False]
_last_ctrl_c: list[float | None] = [None]
_handling_ctrl_c: list[bool] = [False]
_exit_in_progress: list[bool] = [False]
_exit_interrupted: list[bool] = [False]

CTRL_C_DOUBLE_PRESS_WINDOW_S: float = 2.0
_CTRL_C_EXIT_WINDOW: float = CTRL_C_DOUBLE_PRESS_WINDOW_S


def questionary_prompt_style() -> questionary.Style:
    """Shared qmark styling for raw questionary prompts (select/confirm/text/password).

    A function, not a module-level constant, so each call re-resolves ``HIGHLIGHT``
    against the currently active theme rather than freezing it at import time.
    """
    return questionary.Style([("qmark", f"fg:{HIGHLIGHT} bold")])


class _HardQuitInterrupt(KeyboardInterrupt):
    """Raised by explicit quit keys (Ctrl+Q) to bypass the Ctrl+C double-exit guard."""


def _with_escape_cancel(question: questionary.question.Question) -> questionary.question.Question:
    """Prepend Escape handling so it wins over questionary's catch-all bindings."""
    extra = KeyBindings()

    @extra.add(Keys.Escape, eager=True)
    def _escape(event: Any) -> None:
        event.app.exit(result=None)

    app = question.application
    existing: KeyBindingsBase = app.key_bindings or KeyBindings()
    app.key_bindings = merge_key_bindings([extra, existing])
    return question


def _wrap_question_prompt(
    orig: Callable[..., questionary.question.Question],
) -> Callable[..., questionary.question.Question]:
    def wrapped(*args: Any, **kwargs: Any) -> questionary.question.Question:
        return _with_escape_cancel(orig(*args, **kwargs))

    wrapped.__name__ = orig.__name__
    wrapped.__doc__ = orig.__doc__
    wrapped.__qualname__ = getattr(orig, "__qualname__", orig.__name__)
    return wrapped


def install_questionary_escape_cancel() -> None:
    """Make Escape cancel questionary prompts (returns None), consistent across the CLI."""
    if _escape_patch_installed[0]:
        return

    import questionary
    import questionary.prompts.checkbox as checkbox_mod
    import questionary.prompts.confirm as confirm_mod
    import questionary.prompts.password as password_mod
    import questionary.prompts.path as path_mod
    import questionary.prompts.select as select_mod
    import questionary.prompts.text as text_mod

    select_mod.select = _wrap_question_prompt(select_mod.select)
    checkbox_mod.checkbox = _wrap_question_prompt(checkbox_mod.checkbox)
    confirm_mod.confirm = _wrap_question_prompt(confirm_mod.confirm)
    text_mod.text = _wrap_question_prompt(text_mod.text)
    path_mod.path = _wrap_question_prompt(path_mod.path)
    password_mod.password = _wrap_question_prompt(password_mod.password)

    questionary.select = select_mod.select
    questionary.checkbox = checkbox_mod.checkbox
    questionary.confirm = confirm_mod.confirm
    questionary.text = text_mod.text
    questionary.path = path_mod.path
    questionary.password = password_mod.password

    _escape_patch_installed[0] = True


def begin_ctrl_c_exit() -> None:
    """Mark an exit as accepted: the next Ctrl+C ends the process at once.

    Teardown runs with the prompt off screen, so SIGINT arrives at
    :func:`handle_ctrl_c_press` rather than the prompt's own binding. Call this
    wherever an exit is accepted, and that handler interrupts the teardown
    instead of re-arming the double-press gate. One-way for the process.
    """
    _exit_in_progress[0] = True


def ctrl_c_exit_interrupted() -> bool:
    """Whether Ctrl+C has cut an accepted exit's teardown short.

    Teardown stages read this to drop optional blocking work (the closing
    memory pass, a telemetry drain) rather than each waiting to be interrupted
    in turn. Persisting the session is not optional and still runs.
    """
    return _exit_interrupted[0]


def handle_ctrl_c_press() -> None:
    """Handle Ctrl+C from the SIGINT signal handler (between prompts).

    A first press hints and a second exits, except once
    :func:`begin_ctrl_c_exit` has run: from then on the press raises
    :exc:`KeyboardInterrupt` to interrupt teardown.
    """
    if _handling_ctrl_c[0]:
        return
    _handling_ctrl_c[0] = True
    try:
        if _exit_in_progress[0]:
            # Teardown is what the user is waiting on; record the press so the
            # stages after this one skip their own waits too.
            _exit_interrupted[0] = True
            raise KeyboardInterrupt
        now = time.monotonic()
        if _last_ctrl_c[0] is not None and now - _last_ctrl_c[0] <= _CTRL_C_EXIT_WINDOW:
            # This press is the accepted exit, and teardown follows it.
            begin_ctrl_c_exit()
            print("\nGoodbye!", flush=True)
            sys.exit(0)
        _last_ctrl_c[0] = now
        print("\n(Press Ctrl+C again to exit)", flush=True)
    finally:
        _handling_ctrl_c[0] = False


def _with_ctrl_c_double_exit(
    question: questionary.question.Question,
) -> questionary.question.Question:
    """Add Ctrl+C double-exit handling to a questionary prompt."""

    def _patched_ask(*args: Any, **kwargs: Any) -> Any:
        while True:
            try:
                try:
                    asyncio.get_running_loop()
                    in_event_loop = True
                except RuntimeError:
                    in_event_loop = False
                if in_event_loop:
                    result = question.application.run(in_thread=True)
                else:
                    result = question.unsafe_ask(*args, **kwargs)
                _last_ctrl_c[0] = None
                return result
            except KeyboardInterrupt as exc:
                if isinstance(exc, _HardQuitInterrupt):
                    raise
                now = time.monotonic()
                if _last_ctrl_c[0] is not None and now - _last_ctrl_c[0] <= _CTRL_C_EXIT_WINDOW:
                    begin_ctrl_c_exit()
                    print("\nGoodbye!", flush=True)
                    sys.exit(0)
                _last_ctrl_c[0] = now
                print("\n(Press Ctrl+C again to exit)", flush=True)
            except (OSError, KeyError) as exc:
                import logging

                logging.getLogger(__name__).debug(
                    "interactive prompt selector error: %s", exc, exc_info=True
                )
                print(
                    "\nThe interactive prompt could not be displayed due to a "
                    "terminal I/O error on this platform.",
                    flush=True,
                )
                sys.exit(1)

    question.ask = _patched_ask  # type: ignore[method-assign]
    return question


def _wrap_question_ctrl_c(
    orig: Callable[..., questionary.question.Question],
) -> Callable[..., questionary.question.Question]:
    def wrapped(*args: Any, **kwargs: Any) -> questionary.question.Question:
        return _with_ctrl_c_double_exit(orig(*args, **kwargs))

    wrapped.__name__ = orig.__name__
    wrapped.__doc__ = orig.__doc__
    wrapped.__qualname__ = getattr(orig, "__qualname__", orig.__name__)
    return wrapped


def repl_reset_ctrl_c_gate() -> None:
    """Re-arm the double-press gate; an accepted exit is deliberately untouched.

    The Ctrl+C binding ends the prompt with an empty result, so this runs once
    more on the way out. Clearing the exit state here would leave controller
    cleanup unprotected.
    """
    _last_ctrl_c[0] = None


def repl_prompt_ctrl_c_should_exit() -> bool:
    """Arm the REPL Ctrl-C gate or consume a second press as an exit.

    Consuming a press also begins the exit, so a Ctrl+C during the teardown that
    follows ends the process instead of re-arming the gate.
    """
    now = time.monotonic()
    if _last_ctrl_c[0] is not None and now - _last_ctrl_c[0] <= _CTRL_C_EXIT_WINDOW:
        _last_ctrl_c[0] = None
        begin_ctrl_c_exit()
        return True
    _last_ctrl_c[0] = now
    return False


def cli_invocation_name() -> str:
    """Return the basename of the current CLI launcher (for example ``opensre`` or ``o``)."""
    argv0 = sys.argv[0].strip() if sys.argv else ""
    if not argv0:
        return "opensre"
    name = Path(argv0).name
    return name or "opensre"


def print_session_resume_hint(console: Console, session_id: str) -> None:
    """Print REPL and CLI commands that restore ``session_id``.

    Caption stays dim; the copy-pasteable commands use the active theme accent
    so ``/exit`` farewell keeps the same chrome as the rest of the shell.
    """
    cli_name = cli_invocation_name()
    console.print(f"[{DIM}]Resume this session with:[/]")
    console.print(f"[{HIGHLIGHT}]/resume {session_id}[/]")
    console.print(f"[{HIGHLIGHT}]{cli_name} --resume {session_id}[/]")


def repl_prompt_note_ctrl_c(console: Console, session_id: str | None = None) -> bool:
    if repl_prompt_ctrl_c_should_exit():
        console.print()
        if session_id:
            print_session_resume_hint(console, session_id)
        console.print(f"[{HIGHLIGHT}]Goodbye![/]")
        return True
    console.print(f"[{DIM}](Press Ctrl+C again to exit)[/]")
    return False


def install_questionary_ctrl_c_double_exit() -> None:
    """Make Ctrl+C show a hint on first press and exit on second press within 2 s."""
    if _ctrl_c_patch_installed[0]:
        return

    import questionary
    import questionary.prompts.checkbox as checkbox_mod
    import questionary.prompts.confirm as confirm_mod
    import questionary.prompts.password as password_mod
    import questionary.prompts.path as path_mod
    import questionary.prompts.select as select_mod
    import questionary.prompts.text as text_mod

    select_mod.select = _wrap_question_ctrl_c(select_mod.select)
    checkbox_mod.checkbox = _wrap_question_ctrl_c(checkbox_mod.checkbox)
    confirm_mod.confirm = _wrap_question_ctrl_c(confirm_mod.confirm)
    text_mod.text = _wrap_question_ctrl_c(text_mod.text)
    path_mod.path = _wrap_question_ctrl_c(path_mod.path)
    password_mod.password = _wrap_question_ctrl_c(password_mod.password)

    questionary.select = select_mod.select
    questionary.checkbox = checkbox_mod.checkbox
    questionary.confirm = confirm_mod.confirm
    questionary.text = text_mod.text
    questionary.path = path_mod.path
    questionary.password = password_mod.password

    _ctrl_c_patch_installed[0] = True
