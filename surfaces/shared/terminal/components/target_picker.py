"""Searchable record selection with an optional explicit action review."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import Box, Frame, TextArea

from infrastructure.safety.terminal_output import strip_terminal_controls
from surfaces.shared.terminal.components.command_input import command_input_style
from surfaces.shared.terminal.prompt_layout import clip_prompt_text


@dataclass(frozen=True)
class TargetChoice:
    """Stable identity, compact scan text, and details for one selectable record."""

    value: str
    title: str
    summary: str = ""
    details: tuple[str, ...] = ()
    search_text: str = ""
    summary_style: str = "hint"
    compact_summary: str = ""


def build_target_picker(
    choices: Sequence[TargetChoice],
    *,
    title: str,
    heading: str,
    action_label: str,
    confirmation: str = "",
    compact_confirmation: str = "",
) -> Application[str | None]:
    """Select a stable target; a confirmation requires a separate action keystroke."""
    records = tuple(choices)
    searchable = tuple(
        " ".join((item.value, item.title, item.summary, *item.details, item.search_text)).casefold()
        for item in records
    )
    selected = 0
    reviewing = False
    action = 0
    editor = TextArea(height=1, multiline=False, prompt="Search › ")
    matches = list(records)

    def filtered() -> list[TargetChoice]:
        return matches

    def reset_selection(_buffer: object) -> None:
        nonlocal selected, matches
        selected = 0
        query = editor.text.strip().casefold()
        matches = [
            item
            for item, text in zip(records, searchable, strict=True)
            if not query or query in text
        ]

    editor.buffer.on_text_changed += reset_selection

    def current() -> TargetChoice | None:
        matches = filtered()
        return matches[selected] if matches else None

    def clean(text: str) -> str:
        return " ".join(strip_terminal_controls(text).split())

    def content_width() -> int:
        return max(1, min(96, app.output.get_size().columns) - 8)

    def compact() -> bool:
        size = app.output.get_size()
        return size.rows < 24 or (size.columns < 60 and size.rows < 28)

    def text_height(text: str) -> int:
        width = max(1, min(96, app.output.get_size().columns) - 6)
        return max(1, (get_cwidth(text) + width - 1) // width)

    def confirmation_text() -> str:
        return compact_confirmation if compact() and compact_confirmation else confirmation

    def rows() -> StyleAndTextTuples:
        matches = filtered()
        if not matches:
            return [("class:description", "No matches. Clear the search or Esc cancel.")]
        result: StyleAndTextTuples = []
        for index, item in enumerate(matches):
            style = "class:selected" if index == selected else ""
            prefix = "› " if index == selected else "  "
            result.extend(
                [
                    (
                        style + " bold",
                        prefix + clip_prompt_text(clean(item.title), content_width() - 2),
                    ),
                    (style, "\n"),
                    (
                        style + f" class:{item.summary_style}",
                        "  " + clip_prompt_text(clean(item.summary), content_width() - 2),
                    ),
                    (style, "\n"),
                ]
            )
        return result

    selection = FormattedTextControl(rows, get_cursor_position=lambda: Point(0, selected * 2))

    def details() -> StyleAndTextTuples:
        item = current()
        if item is None:
            return []
        summary = item.compact_summary if compact() and item.compact_summary else item.summary
        lines = [
            ("class:hint", f"ID: {clean(item.value)}"),
            (f"class:{item.summary_style}", clean(summary)),
            *(
                []
                if compact()
                else [("class:description", clean(line)) for line in item.details if line.strip()]
            ),
        ]
        return [
            (style, text + ("\n" if index < len(lines) - 1 else ""))
            for index, (style, text) in enumerate(lines)
        ]

    def detail_height() -> Dimension:
        count = sum(text_height(fragment[1].rstrip("\n")) for fragment in details())
        if reviewing:
            reserved = (
                6
                + text_height(review_heading()[0][1])
                + text_height(confirmation_text())
                + text_height(hint())
            )
            reserved += 0 if compact() else 5
        else:
            reserved = 7 + text_height(hint()) + (0 if compact() else 3)
        available = max(1, app.output.get_size().rows - reserved)
        return Dimension(min=min(count, available), preferred=count, max=count)

    def review_heading() -> StyleAndTextTuples:
        item = current()
        limit = content_width() if compact() else content_width() * 2
        return [("bold", clip_prompt_text(clean(item.title), limit) if item else "")]

    def actions() -> StyleAndTextTuples:
        return [
            (
                "class:selected" if index == action else "",
                ("› " if index == action else "  ") + label + "\n",
            )
            for index, label in enumerate((action_label, "Cancel"))
        ]

    review_actions = FormattedTextControl(
        actions, focusable=True, get_cursor_position=lambda: Point(0, action)
    )
    keys = KeyBindings()

    @keys.add("up")
    @keys.add("down")
    @keys.add("tab")
    @keys.add("s-tab")
    def move(event: KeyPressEvent) -> None:
        nonlocal selected, action
        step = -1 if event.key_sequence[0].key in {"up", "s-tab"} else 1
        if reviewing:
            action = (action + step) % 2
        else:
            count = len(filtered())
            selected = (selected + step) % count if count else 0

    @keys.add("enter")
    def accept(event: KeyPressEvent) -> None:
        nonlocal reviewing, action
        item = current()
        if item is None:
            return
        if reviewing:
            event.app.exit(result=item.value if action == 0 else None)
        elif confirmation:
            reviewing = True
            action = 0
            app.layout.focus(review_actions)
        else:
            event.app.exit(result=item.value)

    @keys.add("escape")
    def back(event: KeyPressEvent) -> None:
        nonlocal reviewing
        if reviewing:
            reviewing = False
            app.layout.focus(editor)
        else:
            event.app.exit(result=None)

    @keys.add("c-c")
    @keys.add("c-d")
    def cancel(event: KeyPressEvent) -> None:
        event.app.exit(result=None)

    def spacer() -> ConditionalContainer:
        return ConditionalContainer(Window(height=1), filter=Condition(lambda: not compact()))

    def hint() -> str:
        if compact():
            if reviewing:
                return "Enter confirm · Esc back"
            action_hint = "review" if confirmation else action_label.lower()
            return f"Enter {action_hint} · Esc cancel"
        if reviewing:
            return f"↑↓/Tab · Enter {action_label.lower()} · Esc back"
        action_hint = "review" if confirmation else action_label.lower()
        return f"Type to filter · ↑↓/Tab · Enter {action_hint} · Esc cancel"

    app: Application[str | None] = Application(
        layout=Layout(
            VSplit(
                [
                    Frame(
                        Box(
                            HSplit(
                                [
                                    Window(
                                        FormattedTextControl(
                                            lambda: [
                                                (
                                                    "bold",
                                                    (
                                                        action_label
                                                        if compact()
                                                        else f"Review · {action_label}"
                                                    )
                                                    if reviewing
                                                    else heading,
                                                )
                                            ]
                                        ),
                                        height=1,
                                    ),
                                    spacer(),
                                    ConditionalContainer(
                                        HSplit(
                                            [
                                                editor,
                                                Window(
                                                    FormattedTextControl(
                                                        lambda: [
                                                            (
                                                                "class:hint",
                                                                f"{len(filtered())} of {len(records)}",
                                                            )
                                                        ]
                                                    ),
                                                    height=1,
                                                ),
                                                Window(
                                                    selection,
                                                    height=Dimension(min=2, max=8),
                                                    dont_extend_height=True,
                                                    wrap_lines=True,
                                                ),
                                            ]
                                        ),
                                        filter=Condition(lambda: not reviewing),
                                    ),
                                    ConditionalContainer(
                                        Window(
                                            FormattedTextControl(review_heading),
                                            wrap_lines=True,
                                            dont_extend_height=True,
                                            height=lambda: text_height(review_heading()[0][1]),
                                        ),
                                        filter=Condition(lambda: reviewing),
                                    ),
                                    spacer(),
                                    Window(
                                        FormattedTextControl(details),
                                        wrap_lines=True,
                                        height=detail_height,
                                        dont_extend_height=True,
                                    ),
                                    ConditionalContainer(
                                        HSplit(
                                            [
                                                spacer(),
                                                Window(
                                                    FormattedTextControl(
                                                        lambda: [
                                                            (
                                                                "class:description",
                                                                confirmation_text(),
                                                            )
                                                        ]
                                                    ),
                                                    wrap_lines=True,
                                                    dont_extend_height=True,
                                                    height=lambda: text_height(confirmation_text()),
                                                ),
                                                spacer(),
                                                Window(review_actions, height=2),
                                            ]
                                        ),
                                        filter=Condition(lambda: reviewing),
                                    ),
                                    spacer(),
                                    Window(
                                        FormattedTextControl(lambda: [("class:hint", hint())]),
                                        wrap_lines=True,
                                        dont_extend_height=True,
                                        height=lambda: text_height(hint()),
                                    ),
                                ]
                            ),
                            padding=0,
                            padding_left=2,
                            padding_right=2,
                        ),
                        title=title,
                        width=lambda: min(96, app.output.get_size().columns),
                    ),
                    Window(width=Dimension(min=0, preferred=0)),
                ]
            ),
            focused_element=editor,
        ),
        key_bindings=keys,
        style=command_input_style(),
        full_screen=False,
        erase_when_done=True,
    )
    return app
