"""Interactive prompt helpers (Escape to cancel, etc.)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from prompt_toolkit.key_binding import KeyBindings, KeyBindingsBase, merge_key_bindings
from prompt_toolkit.keys import Keys
from questionary.question import Question

_escape_patch_installed: list[bool] = [False]


def _with_escape_cancel(question: Question) -> Question:
    """Prepend Escape handling so it wins over questionary's catch-all bindings."""
    extra = KeyBindings()

    @extra.add(Keys.Escape, eager=True)
    def _escape(event: Any) -> None:
        event.app.exit(result=None)

    app = question.application
    existing: KeyBindingsBase = app.key_bindings or KeyBindings()
    app.key_bindings = merge_key_bindings([extra, existing])
    return question


def _wrap_question_prompt(orig: Callable[..., Question]) -> Callable[..., Question]:
    def wrapped(*args: Any, **kwargs: Any) -> Question:
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
    import questionary.prompts.path as path_mod
    import questionary.prompts.select as select_mod
    import questionary.prompts.text as text_mod

    select_mod.select = _wrap_question_prompt(select_mod.select)
    checkbox_mod.checkbox = _wrap_question_prompt(checkbox_mod.checkbox)
    confirm_mod.confirm = _wrap_question_prompt(confirm_mod.confirm)
    text_mod.text = _wrap_question_prompt(text_mod.text)
    path_mod.path = _wrap_question_prompt(path_mod.path)

    questionary.select = select_mod.select
    questionary.checkbox = checkbox_mod.checkbox
    questionary.confirm = confirm_mod.confirm
    questionary.text = text_mod.text
    questionary.path = path_mod.path

    _escape_patch_installed[0] = True
