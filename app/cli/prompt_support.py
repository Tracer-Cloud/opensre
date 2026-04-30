"""Interactive prompt helpers (Escape to cancel, etc.)."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from typing import Any

import questionary.question
from prompt_toolkit.key_binding import KeyBindings, KeyBindingsBase, merge_key_bindings
from prompt_toolkit.keys import Keys

_escape_patch_installed: list[bool] = [False]
_ctrl_c_patch_installed: list[bool] = [False]

# Shared timestamp of the last Ctrl+C press (None = never pressed).
_last_ctrl_c: list[float | None] = [None]
_CTRL_C_EXIT_WINDOW: float = 2.0


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


def handle_ctrl_c_press() -> None:
    """Handle Ctrl+C from the SIGINT signal handler (between prompts).

    First call:  prints hint.
    Second call within _CTRL_C_EXIT_WINDOW seconds:  prints Goodbye and exits.
    """
    now = time.monotonic()
    if _last_ctrl_c[0] is not None and now - _last_ctrl_c[0] <= _CTRL_C_EXIT_WINDOW:
        print("\nGoodbye!", flush=True)
        sys.exit(0)
    _last_ctrl_c[0] = now
    print("\n(Press Ctrl+C again to exit)", flush=True)


def _with_ctrl_c_double_exit(
    question: questionary.question.Question,
) -> questionary.question.Question:
    """Add Ctrl+C double-exit handling to a questionary prompt.

    Patches question.ask() to call unsafe_ask() (which does NOT swallow
    KeyboardInterrupt) in a retry loop.  On the first Ctrl+C the hint is
    printed and the prompt is re-displayed; on the second Ctrl+C within
    _CTRL_C_EXIT_WINDOW seconds the process exits.

    We do NOT add extra key bindings — the prompt_toolkit default Ctrl+C
    binding already raises KeyboardInterrupt from application.run(), and
    fighting it with another eager binding causes unpredictable ordering.
    """

    def _patched_ask(*args: Any, **kwargs: Any) -> Any:
        while True:
            try:
                # unsafe_ask() propagates KeyboardInterrupt instead of
                # swallowing it the way ask() does.
                result = question.unsafe_ask(*args, **kwargs)
                _last_ctrl_c[0] = None  # reset on clean exit so next prompt starts fresh
                return result
            except KeyboardInterrupt as exc:
                if isinstance(exc, _HardQuitInterrupt):
                    raise  # Ctrl+Q hard-quit — bypass retry logic
                now = time.monotonic()
                if _last_ctrl_c[0] is not None and now - _last_ctrl_c[0] <= _CTRL_C_EXIT_WINDOW:
                    print("\nGoodbye!", flush=True)
                    sys.exit(0)
                _last_ctrl_c[0] = now
                print("\n(Press Ctrl+C again to exit)", flush=True)
                # Loop: re-run the same application (Application.run() is
                # safe to call again after a clean exit or KeyboardInterrupt).

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
