"""Shared form contracts independent of any command or persistence layer."""

import asyncio

import pytest
from prompt_toolkit.application import Application, create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.screen import Screen
from prompt_toolkit.output import DummyOutput

from surfaces.shared.terminal.components.input_form import (
    InputChoice,
    InputField,
    InputFormError,
    build_input_form,
)


def test_required_searchable_choice_recovers_from_no_matches_without_mutating_input() -> None:
    original = {"target": ""}

    def fields(_values: dict[str, str]) -> list[InputField]:
        return [
            InputField(
                "target",
                "Target",
                required=True,
                searchable=True,
                choices=(InputChoice("alpha", "Alpha"), InputChoice("beta", "Beta")),
            )
        ]

    def validate(values: dict[str, str]) -> str:
        return values["target"]

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        app = build_input_form(
            original,
            title="Pick a target",
            heading="Review target",
            submit_label="Confirm",
            fields=fields,
            validate=validate,
        )
        fragments = [
            text
            for control in app.layout.find_all_controls()
            if isinstance(control, FormattedTextControl)
            for _style, text, *_rest in to_formatted_text(control.text)
        ]
        assert " · required" in fragments
        # No matches: navigation and Enter are harmless; clearing the filter recovers.
        result = app.run(pre_run=lambda: pipe.send_text("missing\r\x1b[B\x1b[A\x15ALP\r\x13"))
    assert result == "alpha"
    assert original == {"target": ""}


def test_incomplete_draft_cannot_reach_submit_validation() -> None:
    def fields(_values: dict[str, str]) -> list[InputField]:
        return [InputField("title", "Title", required=True)]

    def validate(values: dict[str, str]) -> str:
        return values["title"]

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        app = build_input_form(
            {"title": ""},
            title="New item",
            heading="Review item",
            submit_label="Confirm",
            fields=fields,
            validate=validate,
        )
        result = app.run(pre_run=lambda: pipe.send_text("\r\x13Retained title\r\x13\x03"))
    assert result == "Retained title"


@pytest.mark.parametrize("columns,rows", [(40, 12), (90, 30), (160, 30)])
def test_scrolling_review_preserves_visible_actions_and_shortcuts(columns: int, rows: int) -> None:
    class SizedOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=rows, columns=columns)

    original = {f"field-{index}": f"Value {index} 👩🏽‍💻" for index in range(8)}
    original["advanced"] = "Unchanged"

    def fields(_values: dict[str, str]) -> list[InputField]:
        return [
            *(InputField(f"field-{index}", f"Field {index}") for index in range(8)),
            InputField("advanced", "Advanced", advanced=True),
        ]

    def validate(values: dict[str, str]) -> dict[str, str]:
        return values

    frames: list[list[str]] = []
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=SizedOutput()):
        app = build_input_form(
            original,
            title="Edit item",
            heading="Review item",
            submit_label="Save changes",
            fields=fields,
            validate=validate,
        )

        def capture(_app: Application[dict[str, str] | None]) -> None:
            screen = app.renderer.last_rendered_screen
            if screen is None:
                return
            lines = [
                "".join(screen.data_buffer[y][x].char for x in range(columns))
                for y in range(screen.height)
            ]
            if not frames:
                frames.append(lines)
                pipe.send_text("\t" * 9)
                return
            cursor = screen.get_cursor_position(app.layout.current_window)
            if "Save changes" in lines[cursor.y]:
                frames.append(lines)
                pipe.send_text("\r")

        async def stop_if_stalled() -> None:
            await asyncio.sleep(2)
            app.exit(result=None)

        app.after_render += capture
        result = app.run(pre_run=lambda: app.create_background_task(stop_if_stalled()))

    assert len(frames) == 2
    for frame in frames:
        rendered = "\n".join(frame)
        assert len(frame) <= rows
        assert "Save changes" in rendered and "Cancel" in rendered
        assert "Esc cancel" in rendered
    assert result == original


@pytest.mark.parametrize(
    "message",
    [
        "Mode must be one of report, agent.",
        "Choose report or agent. The current mode cannot be used to run this loop.",
    ],
)
def test_submission_error_keeps_actions_and_shortcuts_on_short_terminal(message: str) -> None:
    class SizedOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=12, columns=40)

    original = {f"field-{index}": f"Value {index}" for index in range(8)}
    original["advanced"] = "Unchanged"

    def fields(_values: dict[str, str]) -> list[InputField]:
        return [
            *(InputField(f"field-{index}", f"Field {index}") for index in range(8)),
            InputField("advanced", "Advanced", advanced=True),
        ]

    def validate(_values: dict[str, str]) -> dict[str, str]:
        raise InputFormError(message)

    frames: list[list[str]] = []
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=SizedOutput()):
        app = build_input_form(
            original,
            title="Edit item",
            heading="Review item",
            submit_label="Save changes",
            fields=fields,
            validate=validate,
        )

        def capture(_app: Application[dict[str, str] | None]) -> None:
            screen = app.renderer.last_rendered_screen
            if screen is None or len(frames) == 2:
                return
            lines = [
                "".join(screen.data_buffer[y][x].char for x in range(40))
                for y in range(screen.height)
            ]
            if not frames:
                frames.append(lines)
                pipe.send_text("\x13")
            elif message.split(".")[0] in "\n".join(lines):
                frames.append(lines)
                pipe.send_text("\x03")

        async def stop_if_stalled() -> None:
            await asyncio.sleep(2)
            app.exit(result=None)

        app.after_render += capture
        result = app.run(pre_run=lambda: app.create_background_task(stop_if_stalled()))

    assert result is None and len(frames) == 2
    rendered = "\n".join(frames[-1])
    assert len(frames[-1]) <= 12
    assert "Save changes" in rendered and "Cancel" in rendered
    assert "Esc cancel" in rendered
    assert message.rsplit(" ", 1)[-1] in rendered


def test_form_bounds_background_through_text_choice_review_and_validation() -> None:
    class WideOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=40, columns=212)

    def fields(_values: dict[str, str]) -> list[InputField]:
        return [
            InputField("name", "Name", required=True, description="A short, readable name."),
            InputField(
                "mode",
                "Mode",
                choices=(InputChoice("report", "Report"), InputChoice("agent", "Agent")),
            ),
        ]

    def validate(_values: dict[str, str]) -> dict[str, str]:
        raise InputFormError("Review the delivery settings before saving.")

    frames: list[Screen] = []
    keys = ("Synthetic loop\r", "\t\r", "\x1b[B\r", "\x13", "\x03")
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=WideOutput()):
        app = build_input_form(
            {"name": "", "mode": "report"},
            title="/loops add",
            heading="New loop",
            submit_label="Create loop",
            fields=fields,
            validate=validate,
        )

        def capture(_app: Application[dict[str, str] | None]) -> None:
            screen = app.renderer.last_rendered_screen
            if screen is None or len(frames) >= len(keys):
                return
            frames.append(screen)
            pipe.send_text(keys[len(frames) - 1])

        app.after_render += capture
        assert app.run(pre_run=lambda: app.renderer.report_absolute_cursor_row(1)) is None
    assert len(frames) == len(keys)
    assert app.style is not None
    for screen in frames:
        bottom = next(y for y in range(screen.height) if screen.data_buffer[y][0].char == "└")
        assert screen.data_buffer[bottom][95].char == "┘"
        for y in range(screen.height):
            for x in range(212):
                if y > bottom or x >= 96:
                    cell = screen.data_buffer[y][x]
                    assert not app.style.get_attrs_for_style_str(cell.style).bgcolor
    content = "\n".join(
        "".join(frames[-1].data_buffer[y][x].char for x in range(96))
        for y in range(frames[-1].height)
    )
    assert "Synthetic loop" in content and "agent" in content
    assert "Review the delivery settings before saving." in content
    assert "Create loop" in content and "Esc cancel" in content


def test_form_uses_alternate_screen_so_terminal_reflow_cannot_orphan_rows() -> None:
    class TrackingOutput(DummyOutput):
        def __init__(self) -> None:
            self.entered_alternate_screen = 0
            self.left_alternate_screen = 0

        def enter_alternate_screen(self) -> None:
            self.entered_alternate_screen += 1

        def quit_alternate_screen(self) -> None:
            self.left_alternate_screen += 1

    output = TrackingOutput()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
        app = build_input_form(
            {"name": "audit"},
            title="/records",
            heading="Records",
            submit_label="Close",
            fields=lambda _values: [InputField("name", "Name")],
            validate=lambda _values: None,
        )
        assert app.run(pre_run=lambda: pipe.send_text("\x03")) is None

    assert output.entered_alternate_screen == 1
    assert output.left_alternate_screen == 1
