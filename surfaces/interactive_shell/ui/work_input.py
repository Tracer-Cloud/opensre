"""Editable work-item creation tray."""

from __future__ import annotations

import shlex

from prompt_toolkit.application import Application
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, ScrollablePane, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Button, Frame, Label, TextArea

from core.domain.work_items import WORK_ITEM_PRIORITIES
from surfaces.shared.terminal.components.command_input import command_input_style


def build_work_form(options: dict[str, str]) -> Application[dict[str, str] | None]:
    """Build a work form with optional fields and validation that retains all edits."""
    expanded = bool(options)
    error = ""
    title = TextArea(height=1, multiline=False)
    fields = {
        name: TextArea(text=options.get(name, default), height=1, multiline=False)
        for name, default in (("project", ""), ("priority", "normal"), ("owner", ""), ("due", ""))
    }

    fields["priority"].buffer.completer = WordCompleter(list(WORK_ITEM_PRIORITIES))

    def values() -> dict[str, str]:
        return {
            "title": title.text.strip(),
            **{name: field.text.strip() for name, field in fields.items()},
        }

    def preview() -> str:
        current = values()
        parts = ["/work", "add", current.pop("title") or "<title>"]
        for name, value in current.items():
            if value:
                parts.extend(("--" + name, value))
        return shlex.join(parts)

    def toggle() -> None:
        nonlocal expanded
        expanded = not expanded
        toggle_button.text = "− Options" if expanded else "+ Options"

    def submit() -> None:
        nonlocal error, expanded
        current = values()
        if not current["title"]:
            error = "A title is required."
            app.layout.focus(title)
            return
        if current["priority"].lower() not in WORK_ITEM_PRIORITIES:
            error = "Priority: low, normal, high or urgent."
            expanded = True
            toggle_button.text = "− Options"
            app.layout.focus(fields["priority"])
            return
        app.exit(result=current)

    def cancel() -> None:
        app.exit(result=None)

    toggle_button = Button("− Options" if expanded else "+ Options", handler=toggle)
    optional = HSplit(
        [
            HSplit([Label(label), fields[name]])
            for name, label in (
                ("project", "Project"),
                ("priority", "Priority · low / normal / high / urgent"),
                ("owner", "Owner"),
                ("due", "Due · date or datetime (optional)"),
            )
        ]
    )
    body = HSplit(
        [
            Label("Title (required)"),
            title,
            toggle_button,
            ConditionalContainer(optional, filter=Condition(lambda: expanded)),
            Label("Command preview", style="class:hint"),
            Window(FormattedTextControl(preview), height=2, wrap_lines=True),
            Window(FormattedTextControl(lambda: [("class:error", error)]), height=1),
            Button("Create work item", handler=submit),
            Button("Cancel", handler=cancel),
        ]
    )
    keys = KeyBindings()

    @keys.add("tab")
    def next_field(event: KeyPressEvent) -> None:
        event.app.layout.focus_next()

    @keys.add("s-tab")
    def previous_field(event: KeyPressEvent) -> None:
        event.app.layout.focus_previous()

    @keys.add("c-s")
    def save(_event: KeyPressEvent) -> None:
        submit()

    @keys.add("escape")
    @keys.add("c-c")
    @keys.add("c-d")
    def dismiss(_event: KeyPressEvent) -> None:
        cancel()

    app: Application[dict[str, str] | None] = Application(
        layout=Layout(
            Frame(
                HSplit(
                    [
                        ScrollablePane(
                            body,
                            max_available_height=19,
                            height=lambda: Dimension(
                                min=1, preferred=17 if expanded else 9, max=17 if expanded else 9
                            ),
                        ),
                        Label("Tab next · Ctrl-S create · Esc cancel", style="class:hint"),
                    ]
                ),
                title="/work add",
            ),
            focused_element=title,
        ),
        key_bindings=keys,
        style=command_input_style(),
        full_screen=False,
        erase_when_done=True,
    )
    return app
