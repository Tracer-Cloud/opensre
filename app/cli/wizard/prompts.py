"""Minimal interactive prompts for the onboarding wizard."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from prompt_toolkit.application import Application  # type: ignore[import-not-found]
from prompt_toolkit.key_binding import KeyBindings  # type: ignore[import-not-found]
from prompt_toolkit.keys import Keys  # type: ignore[import-not-found]
from prompt_toolkit.styles import Style  # type: ignore[import-not-found]
from questionary import Choice
from questionary.prompts import common
from questionary.prompts.common import InquirerControl
from questionary.question import Question
from questionary.styles import merge_styles_default


def _base_bindings(
    ic: InquirerControl,
    *,
    allow_toggle: bool = False,
) -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add(Keys.ControlQ, eager=True)
    @bindings.add(Keys.ControlC, eager=True)
    def _abort(event: Any) -> None:
        event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

    def _move_down(_event: Any) -> None:
        ic.select_next()
        while not ic.is_selection_valid():
            ic.select_next()

    def _move_up(_event: Any) -> None:
        ic.select_previous()
        while not ic.is_selection_valid():
            ic.select_previous()

    bindings.add(Keys.Down, eager=True)(_move_down)
    bindings.add(Keys.Up, eager=True)(_move_up)
    bindings.add("j", eager=True)(_move_down)
    bindings.add("k", eager=True)(_move_up)
    bindings.add(Keys.ControlN, eager=True)(_move_down)
    bindings.add(Keys.ControlP, eager=True)(_move_up)
    bindings.add(Keys.ControlI, eager=True)(_move_down)
    bindings.add(Keys.BackTab, eager=True)(_move_up)

    if allow_toggle:

        @bindings.add(" ", eager=True)
        def _toggle(_event: Any) -> None:
            pointed_choice = ic.get_pointed_at().value
            if pointed_choice in ic.selected_options:
                ic.selected_options.remove(pointed_choice)
            else:
                ic.selected_options.append(pointed_choice)

    @bindings.add(Keys.Any)
    def _ignore(_event: Any) -> None:
        """Ignore unrelated keys."""

    return bindings


def select(
    message: str,
    choices: Sequence[Choice],
    *,
    default: Any | None = None,
    style: Style | None = None,
    instruction: str | None = None,
    escape_result: Any | None = None,
) -> Question:
    """Render a single-select prompt with Tab navigation."""
    ic = InquirerControl(
        choices,
        default,
        pointer=">",
        initial_choice=default,
        show_description=False,
        use_arrow_keys=True,
    )

    def _tokens() -> list[tuple[str, str]]:
        tokens = [("class:qmark", "?"), ("class:question", f" {message} ")]
        if ic.is_answered:
            tokens.append(("class:answer", str(ic.get_pointed_at().title)))
        elif instruction:
            tokens.append(("class:instruction", instruction))
        return tokens

    bindings = _base_bindings(ic)

    if escape_result is not None:

        @bindings.add(Keys.Escape, eager=True)
        def _escape(event: Any) -> None:
            event.app.exit(result=escape_result)

    @bindings.add(Keys.ControlM, eager=True)
    def _submit(event: Any) -> None:
        ic.is_answered = True
        event.app.exit(result=ic.get_pointed_at().value)

    return Question(
        Application(
            layout=common.create_inquirer_layout(ic, _tokens),
            key_bindings=bindings,
            style=merge_styles_default([style]),
        )
    )


def checkbox(
    message: str,
    choices: Sequence[Choice],
    *,
    style: Style | None = None,
    instruction: str | None = None,
    initial_choice: str | None = None,
    default: Any | None = None,
) -> Question:
    """Render a multi-select prompt with Tab navigation."""
    # If no explicit initial_choice, place cursor on first choice
    if initial_choice is None and choices:
        first = choices[0]
        initial_choice = first.value if isinstance(first, Choice) else None

    ic = InquirerControl(
        choices,
        pointer=">",
        initial_choice=initial_choice,
        show_description=False,
    )

    # Pre-select any values passed in default
    if default:
        valid_values = {c.value for c in choices if isinstance(c, Choice)}
        ic.selected_options = [v for v in default if v in valid_values]

    def _tokens() -> list[tuple[str, str]]:
        tokens = [("class:qmark", "?"), ("class:question", f" {message} ")]
        if ic.is_answered:
            selected = len(ic.selected_options)
            suffix = "selection" if selected == 1 else "selections"
            tokens.append(("class:answer", f"{selected} {suffix}"))
        elif instruction:
            tokens.append(("class:instruction", instruction))
        return tokens

    bindings = _base_bindings(ic, allow_toggle=True)

    @bindings.add(Keys.ControlM, eager=True)
    def _submit(event: Any) -> None:
        ic.is_answered = True
        event.app.exit(result=[choice.value for choice in ic.get_selected_values()])

    return Question(
        Application(
            layout=common.create_inquirer_layout(ic, _tokens),
            key_bindings=bindings,
            style=merge_styles_default([style]),
        )
    )
