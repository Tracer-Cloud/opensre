"""Editable command forms; callers supply fields and side-effect-free validation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Box, Frame, TextArea

from infrastructure.safety.terminal_output import strip_terminal_controls
from surfaces.shared.terminal.components.command_input import command_input_style
from surfaces.shared.terminal.prompt_layout import clip_prompt_text


@dataclass(frozen=True)
class InputChoice:
    """A stored value and its label; ``None`` opens a custom text editor."""

    value: str | None
    label: str


@dataclass(frozen=True)
class InputField:
    """Current field metadata, recomputed by the caller as the draft changes."""

    name: str
    label: str
    description: str = ""
    status: str = ""
    required: bool = False
    choices: tuple[InputChoice, ...] = ()
    searchable: bool = False
    case_sensitive: bool = True
    summary: str | None = None
    advanced: bool = False
    validate: Callable[[str], None] | None = None
    cancel_on_back_if_empty: bool = False


class InputFormError(ValueError):
    """An actionable validation error, optionally targeting a field to reopen."""

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


def build_input_form[Result](
    values: dict[str, str],
    *,
    title: str,
    heading: str,
    submit_label: str,
    fields: Callable[[dict[str, str]], Sequence[InputField]],
    validate: Callable[[dict[str, str]], Result],
    initial_field: str | None = None,
    initial_review_field: str | None = None,
) -> Application[Result | None]:
    """Return a draft only after explicit submission; validation must not persist it."""
    current = dict(values)
    field_map = {item.name: item for item in fields(dict(current))}
    row = 0
    field = next(iter(field_map), "")
    mode = "summary"
    expanded = False
    error = ""
    choice_index = 0
    editor = TextArea(height=1, multiline=False, prompt="› ")

    def review_fields() -> list[InputField]:
        return [item for item in field_map.values() if item.advanced == expanded]

    def active_field() -> InputField:
        return field_map[field]

    def action_labels() -> tuple[str, ...]:
        if expanded:
            return ("Back to review",)
        more = ("More options…",) if any(item.advanced for item in field_map.values()) else ()
        return (*more, submit_label, "Cancel")

    def choices() -> list[InputChoice]:
        item = active_field()
        query = editor.text.strip().casefold() if item.searchable else ""
        return [
            choice
            for choice in item.choices
            if not query
            or choice.value is None
            or (choice.value != "" and query in choice.label.casefold())
        ]

    def reset_choice(_buffer: object) -> None:
        nonlocal choice_index
        if mode == "choice" and active_field().searchable:
            choice_index = 0

    editor.buffer.on_text_changed += reset_choice

    def marked(index: int, text: str) -> tuple[str, str]:
        style = "class:selected" if index == row else ""
        return style, ("› " if index == row else "  ") + text + "\n"

    def summary_value(item: InputField) -> str:
        value = item.summary
        if value is None:
            value = current[item.name] or ("Required" if item.required else "—")
        return " ".join(strip_terminal_controls(value).split())

    def summary_rows() -> StyleAndTextTuples:
        width = max(1, min(96, app.output.get_size().columns) - 8)
        # Full values are editable; keep the summary short enough to navigate on narrow screens.
        return [
            marked(
                index,
                clip_prompt_text(f"{item.label:<14}{summary_value(item)}", width),
            )
            for index, item in enumerate(review_fields())
        ]

    summary = FormattedTextControl(
        summary_rows,
        focusable=True,
        get_cursor_position=lambda: Point(0, min(row, max(0, len(review_fields()) - 1))),
    )
    actions = FormattedTextControl(
        lambda: [
            marked(len(review_fields()) + index, label)
            for index, label in enumerate(action_labels())
        ],
        focusable=True,
        get_cursor_position=lambda: Point(0, max(0, row - len(review_fields()))),
    )
    selection = FormattedTextControl(
        lambda: [
            (
                "class:selected" if index == choice_index else "",
                ("› " if index == choice_index else "  ")
                + strip_terminal_controls(choice.label)
                + "\n",
            )
            for index, choice in enumerate(choices())
        ],
        focusable=True,
        get_cursor_position=lambda: Point(0, choice_index),
    )

    def focus_row() -> None:
        app.layout.focus(summary if row < len(review_fields()) else actions)

    def show_summary() -> None:
        nonlocal mode, row
        mode = "summary"
        row = min(row, len(review_fields()))
        focus_row()

    def open_field(name: str) -> None:
        nonlocal field, expanded, mode, row, choice_index
        field = name
        item = active_field()
        expanded = item.advanced
        row = next(index for index, item in enumerate(review_fields()) if item.name == name)
        if item.choices:
            mode = "choice"
            editor.text = ""
            selected = current[field]
            candidates = [choice.value for choice in choices()]
            if not item.case_sensitive:
                selected = selected.casefold()
                candidates = [
                    value.casefold() if value is not None else None for value in candidates
                ]
            fallback = candidates.index(None) if selected and None in candidates else 0
            choice_index = candidates.index(selected) if selected in candidates else fallback
            app.layout.focus(editor if item.searchable else selection)
        else:
            mode = "text"
            editor.text = current[field]
            editor.buffer.cursor_position = len(editor.text)
            app.layout.focus(editor)

    def create() -> None:
        nonlocal error
        try:
            for item in field_map.values():
                if item.required and not current[item.name].strip():
                    raise InputFormError(f"{item.label} is required.", field=item.name)
            result = validate(dict(current))
        except InputFormError as exc:
            error = strip_terminal_controls(str(exc))
            if exc.field is not None and exc.field in field_map:
                open_field(exc.field)
            return
        app.exit(result=result)

    keys = KeyBindings()

    @keys.add("enter")
    def accept(event: KeyPressEvent) -> None:
        nonlocal mode, expanded, error, row, field_map
        if mode != "summary":
            item = active_field()
            candidates = choices() if mode == "choice" else []
            if mode == "choice" and not candidates:
                return
            value = candidates[choice_index].value if mode == "choice" else editor.text.strip()
            if value is None:
                draft = editor.text if item.searchable else current[field]
                mode = "text"
                editor.text = draft
                editor.buffer.cursor_position = len(draft)
                app.layout.focus(editor)
                return
            try:
                if item.validate is not None:
                    item.validate(value)
            except InputFormError as exc:
                error = strip_terminal_controls(str(exc))
                return
            current[field] = value
            field_map = {item.name: item for item in fields(dict(current))}
            error = ""
            show_summary()
            return
        items = review_fields()
        if row < len(items):
            error = ""
            open_field(items[row].name)
        else:
            action = action_labels()[row - len(items)]
            if action == submit_label:
                create()
            elif action == "Cancel":
                event.app.exit(result=None)
            else:
                expanded = not expanded
                row = 0
                error = ""
                focus_row()

    @keys.add("up", filter=Condition(lambda: mode != "text"))
    @keys.add("down", filter=Condition(lambda: mode != "text"))
    @keys.add("tab", filter=Condition(lambda: mode != "text"))
    @keys.add("s-tab", filter=Condition(lambda: mode != "text"))
    def move(event: KeyPressEvent) -> None:
        nonlocal row, choice_index
        step = -1 if event.key_sequence[0].key in {"up", "s-tab"} else 1
        if mode == "summary":
            row = (row + step) % (len(review_fields()) + len(action_labels()))
            focus_row()
        else:
            count = len(choices())
            choice_index = (choice_index + step) % count if count else 0

    @keys.add("escape")
    def back(event: KeyPressEvent) -> None:
        nonlocal expanded, row, error
        if mode != "summary":
            if active_field().cancel_on_back_if_empty and not current[field]:
                event.app.exit(result=None)
            else:
                show_summary()
        elif expanded:
            expanded = False
            row = len(review_fields())
            error = ""
            focus_row()
        else:
            event.app.exit(result=None)

    @keys.add("c-c")
    @keys.add("c-d")
    def cancel(event: KeyPressEvent) -> None:
        event.app.exit(result=None)

    @keys.add("c-s", filter=Condition(lambda: mode == "summary" and not expanded))
    def save(_event: KeyPressEvent) -> None:
        create()

    def heading_text() -> StyleAndTextTuples:
        if mode == "summary":
            return [("bold", "More options" if expanded else heading)]
        item = active_field()
        status = "required" if item.required else item.status or "optional"
        return [("bold", item.label), ("class:description", f" · {status}")]

    def hint() -> str:
        if mode == "summary":
            return "↑↓/Tab · Enter select · Esc " + ("back" if expanded else "cancel")
        if mode == "choice":
            prefix = "Type to filter · " if active_field().searchable else ""
            return prefix + "↑↓ choose · Enter select · Esc back"
        return "Enter save field · Esc back"

    def spacer() -> Window:
        return Window(height=Dimension(min=0, preferred=1, max=1))

    def review_height() -> Dimension:
        count = max(1, min(8, len(review_fields())))
        # Reserve borders, heading, one-line shortcuts, the action gap and all actions.
        available = max(1, app.output.get_size().rows - len(action_labels()) - 5)
        return Dimension(min=min(count, available), max=count)

    app: Application[Result | None] = Application(
        layout=Layout(
            Frame(
                Box(
                    HSplit(
                        [
                            Window(FormattedTextControl(heading_text), height=1),
                            spacer(),
                            ConditionalContainer(
                                HSplit(
                                    [
                                        Window(
                                            summary,
                                            height=review_height,
                                            dont_extend_height=True,
                                        ),
                                        Window(height=1),
                                        Window(actions, height=lambda: len(action_labels())),
                                    ]
                                ),
                                filter=Condition(lambda: mode == "summary"),
                            ),
                            ConditionalContainer(
                                Window(
                                    FormattedTextControl(
                                        lambda: [("class:description", active_field().description)]
                                    ),
                                    wrap_lines=True,
                                    dont_extend_height=True,
                                ),
                                filter=Condition(
                                    lambda: mode != "summary" and bool(active_field().description)
                                ),
                            ),
                            ConditionalContainer(
                                editor,
                                filter=Condition(
                                    lambda: (
                                        mode == "text"
                                        or (mode == "choice" and active_field().searchable)
                                    )
                                ),
                            ),
                            ConditionalContainer(
                                Window(
                                    selection,
                                    height=Dimension(min=1, max=6),
                                    dont_extend_height=True,
                                ),
                                filter=Condition(lambda: mode == "choice"),
                            ),
                            ConditionalContainer(
                                Window(
                                    FormattedTextControl(lambda: [("class:error", error)]),
                                    height=Dimension(min=1, max=4),
                                    wrap_lines=True,
                                    dont_extend_height=True,
                                ),
                                filter=Condition(lambda: bool(error)),
                            ),
                            spacer(),
                            Window(
                                FormattedTextControl(lambda: [("class:hint", hint())]),
                                wrap_lines=True,
                                dont_extend_height=True,
                            ),
                        ]
                    ),
                    padding=0,
                    padding_left=2,
                    padding_right=2,
                ),
                title=title,
                width=Dimension(max=96),
            ),
            focused_element=summary,
        ),
        key_bindings=keys,
        style=command_input_style(),
        full_screen=False,
        erase_when_done=True,
    )
    first_missing = next(
        (
            item.name
            for item in field_map.values()
            if item.required and not current[item.name].strip()
        ),
        None,
    )
    if initial_field or first_missing:
        open_field(initial_field or first_missing or "")
    if initial_review_field is not None:
        row = next(
            index for index, item in enumerate(review_fields()) if item.name == initial_review_field
        )
    return app
