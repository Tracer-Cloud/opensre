"""Shared form contracts independent of any command or persistence layer."""

from prompt_toolkit.application import create_app_session
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import DummyOutput

from surfaces.shared.terminal.components.input_form import (
    InputChoice,
    InputField,
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
