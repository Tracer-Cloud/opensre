"""Editable cron task details with explicit, validated creation."""

from __future__ import annotations

import click
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
from surfaces.interactive_shell.ui.cron_input.arguments import (
    cron_options,
    cron_template_defaults,
    missing_cron_fields,
    validate_cron_draft,
    visible_cron_fields,
)
from surfaces.shared.terminal.components.command_input import command_input_style
from surfaces.shared.terminal.prompt_layout import clip_prompt_text


def build_cron_form(values: dict[str, str]) -> Application[list[str] | None]:
    """Edit a draft without side effects; Create is the only successful exit."""
    current = dict(values)
    options = cron_options()
    row = 0
    field = "kind"
    mode = "summary"
    error = ""
    choice_index = 0
    editor = TextArea(height=1, multiline=False, prompt="› ")

    def choices() -> list[str]:
        option = options[field]
        if option.is_flag:
            return ["False", "True"]
        if isinstance(option.type, click.Choice):
            values = [str(value) for value in option.type.choices if value != "work_item_reminder"]
            return [""] + values
        return []

    def field_label(name: str) -> str:
        flag = {
            "cron_expr": "Schedule",
            "timezone": "Timezone",
            "chat_id": "Destination",
            "skill_name": "Skill",
            "window_hours": "Lookback hours",
            "pr_number": "Pull request",
        }.get(name, name.replace("_", " ").capitalize())
        if name in {"kind", "provider"}:
            return f"{flag} (required)"
        if name == "cron_expr":
            return f"{flag} (required unless template)"
        if name == "chat_id":
            return (
                f"{flag} (optional)"
                if current["provider"].lower() == "interactive_shell"
                else f"{flag} (required unless configured)"
            )
        if name == "prompt":
            return f"{flag} (required unless template / agent skill)"
        if name == "skill_name" and current["kind"].lower() == "recurring_skill":
            return f"{flag} (required)"
        if name in {"owner", "repo"}:
            return f"{flag} (required for repository tasks)"
        default = options[name].default
        if name == "mode":
            return f"{flag} (default {cron_template_defaults(current['template']).get('mode', 'report')})"
        return (
            f"{flag} (default {default})"
            if default not in (None, "", False)
            else f"{flag} (optional)"
        )

    def summary_rows() -> StyleAndTextTuples:
        rows: StyleAndTextTuples = []
        for index, name in enumerate(visible_cron_fields(current)):
            value = " ".join(strip_terminal_controls(current[name]).split())
            if not value:
                inherited = cron_template_defaults(current["template"]).get(name)
                value = f"{inherited} (template)" if inherited else "Not set"
            # Full values are editable; keep the summary short enough to navigate on narrow screens.
            preview = clip_prompt_text(value, max(8, min(80, app.output.get_size().columns - 8)))
            rows.append(
                (
                    "class:selected" if index == row else "",
                    ("› " if index == row else "  ") + field_label(name) + "\n  " + preview + "\n",
                )
            )
        for label in ("Create scheduled task", "Cancel"):
            index = len(rows)
            rows.append(
                (
                    "class:selected" if index == row else "",
                    ("› " if index == row else "  ") + label + "\n\n",
                )
            )
        return rows

    summary = FormattedTextControl(
        summary_rows,
        focusable=True,
        get_cursor_position=lambda: Point(0, row * 2 + (row < len(visible_cron_fields(current)))),
    )

    def choice_rows() -> StyleAndTextTuples:
        return [
            (
                "class:selected" if index == choice_index else "",
                ("› " if index == choice_index else "  ") + (value or "Not set / default") + "\n",
            )
            for index, value in enumerate(choices())
        ]

    selection = FormattedTextControl(
        choice_rows, focusable=True, get_cursor_position=lambda: Point(0, choice_index)
    )

    def show_summary() -> None:
        nonlocal mode
        mode = "summary"
        app.layout.focus(summary)

    def create() -> None:
        nonlocal error, row
        try:
            args = validate_cron_draft(current)
        except (click.ClickException, ValueError) as exc:
            error = strip_terminal_controls(
                exc.format_message() if isinstance(exc, click.ClickException) else str(exc)
            )
            names = visible_cron_fields(current)
            missing = missing_cron_fields(current)
            param = exc.param.name if isinstance(exc, click.BadParameter) and exc.param else None
            target = param or (missing[0] if missing else None)
            if target is not None and target in names:
                row = names.index(target)
            return
        app.exit(result=args)

    keys = KeyBindings()

    @keys.add("enter")
    def accept(event: KeyPressEvent) -> None:
        nonlocal mode, field, choice_index, error, row
        if mode != "summary":
            current[field] = choices()[choice_index] if mode == "choice" else editor.text.strip()
            error = ""
            show_summary()
            return
        names = visible_cron_fields(current)
        if row == len(names):
            create()
        elif row > len(names):
            event.app.exit(result=None)
        else:
            field = names[row]
            candidates = choices()
            if candidates:
                mode = "choice"
                selected = current[field]
                option_type = options[field].type
                if isinstance(option_type, click.Choice):
                    selected = option_type.normalize_choice(selected, ctx=None)
                    candidates = [
                        option_type.normalize_choice(value, ctx=None) for value in candidates
                    ]
                choice_index = candidates.index(selected) if selected in candidates else 0
                app.layout.focus(selection)
            else:
                mode = "text"
                editor.text = current[field]
                editor.buffer.cursor_position = len(editor.text)
                app.layout.focus(editor)

    @keys.add("up", filter=Condition(lambda: mode != "text"))
    @keys.add("down", filter=Condition(lambda: mode != "text"))
    @keys.add("tab", filter=Condition(lambda: mode != "text"))
    @keys.add("s-tab", filter=Condition(lambda: mode != "text"))
    def move(event: KeyPressEvent) -> None:
        nonlocal row, choice_index
        step = -1 if event.key_sequence[0].key in {"up", "s-tab"} else 1
        if mode == "summary":
            row = (row + step) % (len(visible_cron_fields(current)) + 2)
        else:
            choice_index = (choice_index + step) % len(choices())

    @keys.add("escape")
    def back(event: KeyPressEvent) -> None:
        if mode == "summary":
            event.app.exit(result=None)
        else:
            show_summary()

    @keys.add("c-c")
    @keys.add("c-d")
    def cancel(event: KeyPressEvent) -> None:
        event.app.exit(result=None)

    @keys.add("c-s", filter=Condition(lambda: mode == "summary"))
    def save(_event: KeyPressEvent) -> None:
        create()

    def heading() -> str:
        return "Review details, then Create." if mode == "summary" else field_label(field)

    def hint() -> str:
        if mode == "summary":
            return "↑↓/Tab move · Enter edit · Ctrl-S create · Esc cancel"
        return (
            "↑↓ choose · Enter save · Esc back"
            if mode == "choice"
            else "Enter save field · Esc back"
        )

    app: Application[list[str] | None] = Application(
        layout=Layout(
            Frame(
                Box(
                    HSplit(
                        [
                            Window(
                                FormattedTextControl(heading),
                                height=Dimension(min=1, max=3),
                                wrap_lines=True,
                                dont_extend_height=True,
                            ),
                            Window(
                                FormattedTextControl(
                                    "Creates a schedule; does not run it now.\nManual loops run at most hourly."
                                ),
                                height=Dimension(min=2, max=4),
                                wrap_lines=True,
                                dont_extend_height=True,
                            ),
                            ConditionalContainer(
                                Window(
                                    summary,
                                    height=Dimension(min=2, preferred=12, max=18),
                                    wrap_lines=True,
                                    dont_extend_height=True,
                                ),
                                filter=Condition(lambda: mode == "summary"),
                            ),
                            ConditionalContainer(editor, filter=Condition(lambda: mode == "text")),
                            ConditionalContainer(
                                Window(
                                    selection,
                                    height=Dimension(min=2, max=8),
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
                            Window(
                                FormattedTextControl(lambda: [("class:hint", hint())]),
                                wrap_lines=True,
                                dont_extend_height=True,
                            ),
                        ]
                    ),
                    padding=0,
                    padding_left=1,
                    padding_right=1,
                ),
                title="/cron add",
            ),
            focused_element=summary,
        ),
        key_bindings=keys,
        style=command_input_style(),
        full_screen=False,
        erase_when_done=True,
    )
    return app
