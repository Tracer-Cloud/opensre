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
from questionary.prompts.common import (
    INDICATOR_SELECTED,
    INDICATOR_UNSELECTED,
    InquirerControl,
    Separator,
)
from questionary.question import Question
from questionary.styles import merge_styles_default


class _CheckboxControl(InquirerControl):
    """Render checked items neutrally unless they are the active row."""

    def _get_choice_tokens(self) -> list[tuple[str, str]]:  # type: ignore[override]
        tokens: list[tuple[str, str]] = []

        for index, choice in enumerate(self.filtered_choices):
            selected = choice.value in self.selected_options
            is_pointed = index == self.pointed_at

            if is_pointed:
                if self.pointer is not None:
                    tokens.append(("class:pointer", f" {self.pointer} "))
                else:
                    tokens.append(("class:text", " " * 3))
                tokens.append(("[SetCursorPosition]", ""))
            else:
                pointer_length = len(self.pointer) if self.pointer is not None else 1
                tokens.append(("class:text", " " * (2 + pointer_length)))

            if isinstance(choice, Separator):
                tokens.append(("class:separator", f"{choice.title}"))
                tokens.append(("", "\n"))
                continue

            if choice.disabled:
                tokens.append(("class:disabled", f"- {choice.title}"))
                if not isinstance(choice.disabled, bool):
                    tokens.append(("class:disabled", f" ({choice.disabled})"))
                tokens.append(("", "\n"))
                continue

            indicator = (
                f"{INDICATOR_SELECTED if selected else INDICATOR_UNSELECTED} "
                if self.use_indicator
                else ""
            )
            indicator_class = (
                "class:highlighted"
                if is_pointed
                else ("class:selected" if selected else "class:text")
            )
            text_class = "class:highlighted" if is_pointed else "class:text"

            if is_pointed:
                tokens.append((indicator_class, indicator))
                if isinstance(choice.title, list):
                    for _style, text in choice.title:
                        tokens.append((text_class, text))
                else:
                    tokens.append((text_class, f"{choice.title}"))
            else:
                tokens.append((indicator_class, indicator))
                if isinstance(choice.title, list):
                    for _style, text in choice.title:
                        tokens.append((text_class, text))
                else:
                    tokens.append((text_class, f"{choice.title}"))

            tokens.append(("", "\n"))

        if tokens:
            tokens.pop()
        return tokens


def _layout_kwargs(*, input: Any | None = None, output: Any | None = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if input is not None:
        kwargs["input"] = input
    if output is not None:
        kwargs["output"] = output
    return kwargs


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
        _event.app.invalidate()

    def _move_up(_event: Any) -> None:
        ic.select_previous()
        while not ic.is_selection_valid():
            ic.select_previous()
        _event.app.invalidate()

    bindings.add(Keys.Down, eager=True)(_move_down)
    bindings.add(Keys.Up, eager=True)(_move_up)
    bindings.add("j", eager=True)(_move_down)
    bindings.add("k", eager=True)(_move_up)

    # PTY smoke tests write many `j` bytes in one burst. Depending on how the terminal driver and
    # prompt_toolkit coalesce input, that burst can be interpreted as a single multi-key sequence
    # instead of individual `j` presses. Register a bulk navigation sequence so the wizard remains
    # reliable under PTY-driven automation.
    @bindings.add(*(["j"] * 15), eager=True)
    def _bulk_move_down(event: Any) -> None:
        for _ in range(15):
            _move_down(event)

    bindings.add(Keys.ControlN, eager=True)(_move_down)
    bindings.add(Keys.ControlP, eager=True)(_move_up)
    bindings.add(Keys.ControlI, eager=True)(_move_down)
    bindings.add(Keys.BackTab, eager=True)(_move_up)
    bindings.add(Keys.Right, eager=True)(_move_down)
    bindings.add(Keys.Left, eager=True)(_move_up)

    # In PTY-driven tests we sometimes "paste" many `j` presses as a single burst write.
    # When terminals enable bracketed paste, prompt_toolkit reports this as a BracketedPaste
    # key event with the full pasted text in `event.data`. Treat a paste of `j` characters as
    # repeated downward navigation so automation remains deterministic.
    @bindings.add(Keys.BracketedPaste, eager=True)
    def _handle_bracketed_paste(event: Any) -> None:
        text = getattr(event, "data", "") or ""
        if text and set(text) == {"j"}:
            for _ in range(len(text)):
                _move_down(event)
            return

    if allow_toggle:

        @bindings.add(" ", eager=True)
        def _toggle(_event: Any) -> None:
            pointed_choice = ic.get_pointed_at().value
            if pointed_choice in ic.selected_options:
                ic.selected_options.remove(pointed_choice)
            else:
                ic.selected_options.append(pointed_choice)
            _event.app.invalidate()

    return bindings


def select(
    message: str,
    choices: Sequence[Choice],
    *,
    default: Any | None = None,
    style: Style | None = None,
    instruction: str | None = None,
    escape_result: Any | None = None,
    input: Any | None = None,
    output: Any | None = None,
) -> Question:
    """Render a single-select prompt with navigation-only movement."""
    ic = InquirerControl(
        choices,
        None,
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

    @bindings.add(Keys.Escape, eager=True)
    def _escape(event: Any) -> None:
        event.app.exit(result=escape_result)

    @bindings.add(Keys.ControlM, eager=True)
    def _submit(event: Any) -> None:
        ic.is_answered = True
        event.app.exit(result=ic.get_pointed_at().value)

    return Question(
        Application(
            layout=common.create_inquirer_layout(
                ic,
                _tokens,
                **_layout_kwargs(input=input, output=output),
            ),
            key_bindings=bindings,
            style=merge_styles_default([style]),
            input=input,
            output=output,
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
    input: Any | None = None,
    output: Any | None = None,
) -> Question:
    """Render a multi-select prompt with explicit space-to-toggle behavior."""
    # If no explicit initial_choice, place cursor on first choice
    if initial_choice is None and choices:
        first = choices[0]
        initial_choice = first.value if isinstance(first, Choice) else None

    ic = _CheckboxControl(
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

    @bindings.add(Keys.Escape, eager=True)
    def _escape_checkbox(event: Any) -> None:
        event.app.exit(result=None)

    @bindings.add(Keys.ControlM, eager=True)
    def _submit(event: Any) -> None:
        ic.is_answered = True
        event.app.exit(result=[choice.value for choice in ic.get_selected_values()])

    return Question(
        Application(
            layout=common.create_inquirer_layout(
                ic,
                _tokens,
                **_layout_kwargs(input=input, output=output),
            ),
            key_bindings=bindings,
            style=merge_styles_default([style]),
            input=input,
            output=output,
        )
    )
