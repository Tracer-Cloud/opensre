"""Editable command forms; callers supply fields and side-effect-free validation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import (
    ConditionalContainer,
    HSplit,
    Layout,
    ScrollOffsets,
    VSplit,
    Window,
)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import AfterInput, ConditionalProcessor
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import Box, Frame, TextArea
from rich.text import Text

from infrastructure.safety.terminal_output import strip_terminal_controls
from surfaces.shared.terminal.components.command_input import command_input_style
from surfaces.shared.terminal.components.record_choices import RecordChoices
from surfaces.shared.terminal.prompt_layout import clip_prompt_text
from surfaces.shared.terminal.tables.records import RecordColumn, RecordRow

_RECORD_DETAIL_PREVIEW_WIDTH = 120


def _record_detail_preview(text: str, query: str = "") -> str:
    """Keep a matching segment visible when a record detail needs truncation."""
    visible = strip_terminal_controls(text)
    preview = clip_prompt_text(visible, _RECORD_DETAIL_PREVIEW_WIDTH)
    if not query or query in preview.casefold():
        return preview
    folded_match = visible.casefold().find(query)
    if folded_match < 0:
        return preview
    folded_offset = 0
    source_index = 0
    for index, char in enumerate(visible):
        folded_offset += len(char.casefold())
        if folded_offset > folded_match:
            source_index = index
            break
    return clip_prompt_text(f"…{visible[source_index:]}", _RECORD_DETAIL_PREVIEW_WIDTH)


@dataclass(frozen=True)
class InputChoice:
    """A stored value and its label; ``None`` opens a custom text editor."""

    value: str | None
    label: str
    summary: str = ""
    details: tuple[str, ...] = ()
    search_text: str = ""
    summary_style: str = "hint"
    compact_summary: str = ""
    compact_details: tuple[str, ...] = ()
    minimal_details: tuple[str, ...] = ()
    record: RecordRow | None = None


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
    record_choices: bool = False
    record_columns: tuple[RecordColumn, ...] = ()


class InputFormError(ValueError):
    """An actionable validation error, optionally targeting a field to reopen."""

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class RecordAction[Result]:
    """An explicit record shortcut; returning a result closes the browser."""

    key: str
    label: str
    handler: Callable[[InputChoice | None], Awaitable[Result | None]]
    enabled: Callable[[InputChoice | None], bool] | None = None
    progress_label: str = "Working…"


def build_input_form[Result](
    values: dict[str, str],
    *,
    title: str,
    heading: str,
    submit_label: str,
    fields: Callable[[dict[str, str]], Sequence[InputField]],
    validate: Callable[[dict[str, str]], Result | None],
    initial_field: str | None = None,
    initial_review_field: str | None = None,
    submit_on_choice: bool = False,
    focus_submit_on_choice: bool = False,
    confirmation: str = "",
    compact_confirmation: str = "",
    browse_only: bool = False,
    max_width: int = 96,
    record_actions: Sequence[RecordAction[Result]] = (),
    load_records: Callable[[Callable[[], None]], Awaitable[None]] | None = None,
) -> Application[Result | None]:
    """Validate drafts; optional record loading runs in an application-owned task."""
    current = dict(values)
    field_map = {item.name: item for item in fields(dict(current))}
    row = 0
    field = next(iter(field_map), "")
    mode = "summary"
    expanded = False
    error = ""
    choice_index = 0
    busy = False
    progress_label = "Working…"
    pinned_record: str | None = None
    search_revision = 0

    def record_mode() -> bool:
        item = field_map.get(field)
        return item is not None and item.record_choices

    editor = TextArea(
        height=1,
        multiline=False,
        prompt=lambda: "Search › " if record_mode() else "› ",
        input_processors=[
            ConditionalProcessor(
                AfterInput([("class:hint", "Filter items…")]),
                filter=Condition(lambda: record_mode() and not editor.text),
            )
        ],
    )

    def search_catalog() -> dict[str, tuple[tuple[InputChoice, str], ...]]:
        return {
            item.name: tuple(
                (
                    choice,
                    " ".join(
                        (
                            (choice.value or "") if not browse_only else "",
                            choice.label,
                            choice.summary,
                            *choice.details,
                            choice.search_text,
                            *(
                                (cell.plain for cell in choice.record.cells)
                                if choice.record
                                else ()
                            ),
                        )
                    ).casefold()
                    if item.record_choices
                    else choice.label.casefold(),
                )
                for choice in item.choices
            )
            for item in field_map.values()
        }

    choice_catalog = search_catalog()

    def record_catalog() -> dict[str, RecordChoices]:
        return {
            item.name: RecordChoices(
                item.record_columns,
                {
                    choice.value: choice.record
                    for choice in item.choices
                    if choice.value is not None and choice.record is not None
                },
            )
            for item in field_map.values()
            if item.record_columns
        }

    record_views = record_catalog()

    def frame_width() -> int:
        return min(max(1, max_width), app.output.get_size().columns)

    def record_width() -> int:
        return max(1, frame_width() - 6)

    def record_header() -> StyleAndTextTuples:
        view = record_views.get(field)
        return view.header(record_width()) if view is not None else []

    def record_row_height() -> int:
        view = record_views.get(field)
        return view.row_height(record_width()) if view is not None else 2

    def choice_cursor() -> Point:
        if not record_mode():
            return Point(0, choice_index)
        gap = int(field in record_views and not compact_records())
        return Point(0, choice_index * (record_row_height() + gap))

    def review_fields() -> list[InputField]:
        return [item for item in field_map.values() if item.advanced == expanded]

    def active_field() -> InputField:
        return field_map[field]

    def compact_records() -> bool:
        size = app.output.get_size()
        return record_mode() and (size.rows < 24 or (size.columns < 60 and size.rows < 28))

    def record_count_visible() -> bool:
        return app.output.get_size().rows >= 16

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
            for choice, search_text in choice_catalog[field]
            if not query
            or choice.value is None
            or choice.value == pinned_record
            or (choice.value != "" and query in search_text)
        ]

    def reset_choice(_buffer: object) -> None:
        nonlocal choice_index, pinned_record, search_revision
        search_revision += 1
        pinned_record = None
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
        width = max(1, frame_width() - 8)
        if record_mode():
            limit = width if compact_records() else width * 2
            return [
                (style + " bold", text)
                for index, item in enumerate(review_fields())
                for style, text in (
                    marked(index, clip_prompt_text(summary_value(item), limit - 2)),
                )
            ]
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

    def choice_rows() -> StyleAndTextTuples:
        candidates = choices()
        if record_mode() and not candidates:
            close = "close" if browse_only else "cancel"
            return [("class:description", f"No matches. Clear the search or Esc {close}.")]
        width = max(1, frame_width() - 10)
        rows: StyleAndTextTuples = []
        view = record_views.get(field)
        for index, choice in enumerate(candidates):
            if view is not None and choice.value is not None:
                if index and not compact_records():
                    rows.append(("", "\n"))
                handler = select_record(choice.value)
                rows.extend(
                    (style, text, handler)
                    for style, text, *_rest in view.row(
                        choice.value, record_width(), selected=index == choice_index
                    )
                )
                continue
            style = "class:selected" if index == choice_index else ""
            label = strip_terminal_controls(choice.label)
            if record_mode():
                label = clip_prompt_text(" ".join(label.split()), width)
            rows.append(
                (
                    style + (" bold" if record_mode() else ""),
                    ("› " if index == choice_index else "  ") + label + "\n",
                )
            )
            if record_mode():
                rows.append(
                    (
                        style + f" class:{choice.summary_style}",
                        "  "
                        + clip_prompt_text(
                            " ".join(strip_terminal_controls(choice.summary).split()), width
                        )
                        + "\n",
                    )
                )
        if view is not None and rows and rows[-1][1] == "\n":
            rows.pop()
        return rows

    def select_record(value: str) -> Callable[[MouseEvent], object]:
        def clicked(event: MouseEvent) -> object:
            nonlocal choice_index
            if event.event_type != MouseEventType.MOUSE_UP:
                return NotImplemented
            candidates = [candidate.value for candidate in choices()]
            if value not in candidates:
                return None
            choice_index = candidates.index(value)
            app.layout.focus(editor if active_field().searchable else selection)
            return None

        return clicked

    selection = FormattedTextControl(
        choice_rows,
        focusable=True,
        get_cursor_position=choice_cursor,
    )

    def selected_record() -> InputChoice | None:
        if not record_mode():
            return None
        candidates = choices()
        if mode == "choice":
            return candidates[choice_index] if candidates else None
        return next(
            (choice for choice in active_field().choices if choice.value == current[field]), None
        )

    def record_details() -> StyleAndTextTuples:
        choice = selected_record()
        if choice is None:
            return []
        summary = (
            choice.compact_summary
            if compact_records() and choice.compact_summary
            else choice.summary
        )
        inline_id = (
            mode == "choice"
            and bool(record_header())
            and any(column.header == "ID" for column in active_field().record_columns)
        )
        if browse_only and choice.record is not None:
            lines = [(str(line.style), line.plain) for line in choice.record.metadata]
            query = editor.text.strip().casefold()
            matched: list[tuple[Text, bool]] = []
            remaining: list[tuple[Text, bool]] = []
            for line in choice.record.details:
                if not line.plain.strip():
                    continue
                is_match = bool(query and query in line.plain.casefold())
                (matched if is_match else remaining).append((line, is_match))
            record_details = [*matched, *remaining] if matched else remaining
            if compact_records() and not record_actions:
                record_details = matched[:1]
            lines.extend(
                (str(line.style), _record_detail_preview(line.plain, query if is_match else ""))
                for line, is_match in record_details
            )
        else:
            lines = [] if inline_id else [("class:hint", f"ID: {choice.value or ''}")]
        if summary:
            lines.append((f"class:{choice.summary_style}", summary))
        details = choice.compact_details if compact_records() else choice.details
        if compact_records() and app.output.get_size().rows < 16 and choice.minimal_details:
            details = choice.minimal_details
        lines.extend(("class:description", detail) for detail in details if detail.strip())
        return [
            (
                style,
                " ".join(strip_terminal_controls(text).split())
                + ("\n" if index < len(lines) - 1 else ""),
            )
            for index, (style, text) in enumerate(lines)
        ]

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
        if item.choices or item.record_choices:
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
        if browse_only:
            return
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
        nonlocal mode, expanded, error, row, field_map, choice_catalog, record_views
        if browse_only:
            return
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
            choice_catalog = search_catalog()
            record_views = record_catalog()
            error = ""
            if submit_on_choice and mode == "choice":
                create()
                return
            show_summary()
            if focus_submit_on_choice:
                row = len(review_fields())
                focus_row()
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
            if active_field().record_choices or (
                active_field().cancel_on_back_if_empty and not current[field]
            ):
                event.app.exit(result=None)
            else:
                show_summary()
        elif expanded:
            expanded = False
            row = len(review_fields())
            error = ""
            focus_row()
        elif record_mode() and focus_submit_on_choice:
            open_field(field)
        else:
            event.app.exit(result=None)

    @keys.add("c-c")
    @keys.add("c-d")
    def cancel(event: KeyPressEvent) -> None:
        event.app.exit(result=None)

    @keys.add("c-s", filter=Condition(lambda: mode == "summary" and not expanded))
    def save(_event: KeyPressEvent) -> None:
        create()

    def available_action[ActionResult](action: RecordAction[ActionResult]) -> bool:
        size = app.output.get_size()
        return (
            record_mode()
            and size.columns >= 40
            and size.rows >= 12
            and (action.enabled is None or action.enabled(selected_record()))
        )

    def refresh_records() -> None:
        nonlocal field_map, choice_catalog, record_views, choice_index
        if app.is_done:
            return
        selected = selected_record()
        field_map = {item.name: item for item in fields(dict(current))}
        choice_catalog = search_catalog()
        record_views = record_catalog()
        candidates = [candidate.value for candidate in choices()]
        choice_index = (
            candidates.index(selected.value)
            if selected is not None and selected.value in candidates
            else 0
        )
        app.invalidate()

    def bind_action[ActionResult](action: RecordAction[ActionResult]) -> None:
        @keys.add(action.key, filter=Condition(lambda: not busy and available_action(action)))
        def act(event: KeyPressEvent) -> None:
            nonlocal busy, error, progress_label
            choice = selected_record()
            action_search_revision = search_revision
            busy = True
            progress_label = action.progress_label
            error = ""

            async def perform() -> None:
                nonlocal busy, error, pinned_record
                try:
                    result = await action.handler(choice)
                    if event.app.is_done:
                        return
                    if result is not None:
                        event.app.exit(result=result)
                        return
                    if search_revision == action_search_revision:
                        pinned_record = choice.value if choice is not None else None
                    refresh_records()
                except InputFormError as exc:
                    error = strip_terminal_controls(str(exc))
                finally:
                    busy = False
                    event.app.invalidate()

            event.app.create_background_task(perform())

    for action in record_actions:
        bind_action(action)

    def heading_text() -> StyleAndTextTuples:
        if mode == "summary":
            return [("bold", "More options" if expanded else heading)]
        item = active_field()
        if item.record_choices:
            count = f"{len(choices())} of {len(item.choices)} items"
            gap = record_width() - get_cwidth(item.label) - get_cwidth(count)
            if record_count_visible() and gap >= 2:
                return [("bold", item.label), ("class:hint", " " * gap + count)]
            return [("bold", item.label)]
        status = "required" if item.required else item.status or "optional"
        return [("bold", item.label), ("class:description", f" · {status}")]

    def hint() -> str:
        if record_mode():
            if browse_only:
                if busy:
                    return f"{progress_label} · Esc close"
                actions = " · ".join(
                    action.label for action in record_actions if available_action(action)
                )
                return "↑↓/Tab move · " + (actions + " · " if actions else "") + "Esc close"
            if mode == "summary":
                return (
                    "Enter confirm · Esc back"
                    if compact_records()
                    else f"↑↓/Tab · Enter {submit_label.lower()} · Esc back"
                )
            action = "review" if confirmation else submit_label.lower()
            return (
                f"Enter {action} · Esc cancel"
                if compact_records()
                else f"↑↓/Tab move · Enter {action} · Esc cancel"
            )
        if mode == "summary":
            return "↑↓/Tab · Enter select · Esc " + ("back" if expanded else "cancel")
        if mode == "choice":
            prefix = "Type to filter · " if active_field().searchable else ""
            return prefix + "↑↓ choose · Enter select · Esc back"
        return "Enter save field · Esc back"

    def spacer() -> ConditionalContainer:
        return ConditionalContainer(
            Window(height=Dimension(min=0, preferred=1, max=1)),
            filter=Condition(lambda: not compact_records()),
        )

    def shortcut_text() -> StyleAndTextTuples:
        if not record_mode():
            return [("class:hint", hint())]
        words = hint().split()
        return [
            (
                "class:record-marker"
                if word in {"↑↓/Tab", "Enter", "Esc"}
                else "class:description",
                word + (" " if index < len(words) - 1 else ""),
            )
            for index, word in enumerate(words)
        ]

    def wrapped_rows(text: str) -> int:
        width = max(1, frame_width() - 6)
        return sum(
            max(1, (get_cwidth(line) + width - 1) // width) for line in text.splitlines() or ("",)
        )

    def shortcut_height() -> Dimension:
        rows = wrapped_rows(hint())
        return Dimension(min=rows, max=rows)

    def error_height() -> Dimension:
        if not error:
            return Dimension(min=0, max=0)
        # Summary actions, one review row, heading, borders and the action gap stay visible.
        reserved = 5 + len(action_labels()) if mode == "summary" else 5
        available = max(1, app.output.get_size().rows - wrapped_rows(hint()) - reserved)
        rows = min(4, wrapped_rows(error), available)
        return Dimension(min=rows, max=rows)

    def confirmation_text() -> str:
        return compact_confirmation if compact_records() and compact_confirmation else confirmation

    def confirmation_height() -> Dimension:
        count = wrapped_rows(confirmation_text())
        return Dimension(min=count, max=count)

    def record_detail_height() -> Dimension:
        count = sum(wrapped_rows(fragment[1].rstrip("\n")) for fragment in record_details())
        if mode == "summary":
            reserved = (
                3
                + review_height().min
                + len(action_labels())
                + wrapped_rows(hint())
                + wrapped_rows(confirmation_text())
            )
            reserved += 0 if compact_records() else 5
        else:
            reserved = (
                6
                + (1 if compact_records() else 2) * int(bool(record_header()))
                + wrapped_rows(hint())
                + (0 if compact_records() else 4)
            )
        available = max(1, app.output.get_size().rows - reserved - error_height().min)
        return Dimension(min=min(count, available), preferred=count, max=count)

    def review_height() -> Dimension:
        if record_mode():
            count = sum(wrapped_rows(fragment[1].rstrip("\n")) for fragment in summary_rows())
            return Dimension(min=count, max=count)
        count = max(1, min(8, len(review_fields())))
        # Keep diagnostics and the complete footer outside the scrolling review.
        available = max(
            1,
            app.output.get_size().rows
            - len(action_labels())
            - wrapped_rows(hint())
            - error_height().min
            - 6,
        )
        visible = min(count, available)
        return Dimension(min=visible, max=visible)

    summary_window = Window(
        summary, height=review_height, dont_extend_height=True, wrap_lines=Condition(record_mode)
    )
    actions_window = Window(actions, height=lambda: len(action_labels()))
    frame = Frame(
        Box(
            HSplit(
                [
                    Window(FormattedTextControl(heading_text), height=1),
                    spacer(),
                    ConditionalContainer(
                        HSplit(
                            [
                                summary_window,
                                Window(height=1),
                                actions_window,
                            ]
                        ),
                        filter=Condition(lambda: mode == "summary" and not record_mode()),
                    ),
                    ConditionalContainer(
                        summary_window,
                        filter=Condition(lambda: mode == "summary" and record_mode()),
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
                                mode == "text" or (mode == "choice" and active_field().searchable)
                            )
                        ),
                    ),
                    ConditionalContainer(
                        spacer(),
                        filter=Condition(lambda: mode == "choice" and record_mode()),
                    ),
                    ConditionalContainer(
                        Window(
                            FormattedTextControl(record_header),
                            height=lambda: 1 if compact_records() else 2,
                        ),
                        filter=Condition(lambda: mode == "choice" and bool(record_header())),
                    ),
                    ConditionalContainer(
                        Window(
                            selection,
                            height=lambda: (
                                Dimension(min=2, max=12)
                                if record_mode()
                                else Dimension(min=1, max=6)
                            ),
                            dont_extend_height=True,
                            wrap_lines=Condition(record_mode),
                            scroll_offsets=ScrollOffsets(
                                bottom=lambda: record_row_height() - 1 if record_mode() else 0
                            ),
                        ),
                        filter=Condition(lambda: mode == "choice"),
                    ),
                    ConditionalContainer(
                        HSplit(
                            [
                                spacer(),
                                Window(
                                    FormattedTextControl(record_details),
                                    height=record_detail_height,
                                    wrap_lines=True,
                                    dont_extend_height=True,
                                ),
                            ]
                        ),
                        filter=Condition(lambda: selected_record() is not None),
                    ),
                    ConditionalContainer(
                        HSplit(
                            [
                                spacer(),
                                Window(
                                    FormattedTextControl(
                                        lambda: [("class:description", confirmation_text())]
                                    ),
                                    height=confirmation_height,
                                    wrap_lines=True,
                                    dont_extend_height=True,
                                ),
                            ]
                        ),
                        filter=Condition(lambda: mode == "summary" and bool(confirmation)),
                    ),
                    ConditionalContainer(
                        HSplit([spacer(), actions_window]),
                        filter=Condition(lambda: mode == "summary" and record_mode()),
                    ),
                    ConditionalContainer(
                        Window(
                            FormattedTextControl(lambda: [("class:error", error)]),
                            height=error_height,
                            wrap_lines=True,
                            dont_extend_height=True,
                        ),
                        filter=Condition(lambda: bool(error)),
                    ),
                    spacer(),
                    Window(
                        FormattedTextControl(shortcut_text),
                        height=shortcut_height,
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
        width=frame_width,
    )

    def frame_height() -> Dimension:
        # CPR and earlier renders can reserve extra rows; keep paint inside the frame.
        size = app.output.get_size()
        height = frame.container.preferred_height(frame_width(), size.rows).preferred
        return Dimension(preferred=height, max=height)

    app: Application[Result | None] = Application(
        layout=Layout(
            HSplit(
                [
                    VSplit(
                        [frame, Window(width=Dimension(min=0, preferred=0), height=0)],
                        height=frame_height,
                    )
                ]
            ),
            focused_element=summary,
        ),
        key_bindings=keys,
        style=command_input_style(),
        # Main-buffer reflow can strand old inline rows after a shrink/expand cycle.
        full_screen=True,
        erase_when_done=True,
        mouse_support=Condition(record_mode),
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
    if load_records is not None:

        async def load() -> None:
            await load_records(refresh_records)

        def start_loading() -> None:
            app.create_background_task(load())

        app.pre_run_callables.append(start_loading)
    return app
