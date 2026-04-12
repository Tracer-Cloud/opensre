from __future__ import annotations

from prompt_toolkit.input.defaults import create_pipe_input  # type: ignore[import-not-found]
from prompt_toolkit.keys import Keys  # type: ignore[import-not-found]
from prompt_toolkit.output import DummyOutput  # type: ignore[import-not-found]
from questionary import Choice

from app.cli.wizard.prompts import checkbox, select


def test_select_prompt_registers_tab_navigation() -> None:
    question = select(
        "Provider", [Choice("Anthropic", value="anthropic"), Choice("OpenAI", value="openai")]
    )
    key_bindings = question.application.key_bindings
    assert key_bindings is not None
    bindings = {binding.keys for binding in key_bindings.bindings}

    assert (Keys.ControlI,) in bindings
    assert (Keys.BackTab,) in bindings
    assert (Keys.Right,) in bindings
    assert (Keys.Left,) in bindings


def test_select_prompt_tab_navigation_changes_selection() -> None:
    # Simulate pressing Tab to move from the first to the second option, then Enter to select it.
    choices = [Choice("Anthropic", value="anthropic"), Choice("OpenAI", value="openai")]
    question = select("Provider", choices)

    with create_pipe_input() as pipe_input:
        # Tab followed by Enter
        pipe_input.send_text("\t\n")

        application = question.application
        application.input = pipe_input
        application.output = DummyOutput()

        result = application.run()

    assert result == "openai"


def test_select_prompt_escape_cancels() -> None:
    choices = [Choice("Anthropic", value="anthropic"), Choice("OpenAI", value="openai")]
    question = select("Provider", choices)

    with create_pipe_input() as pipe_input:
        pipe_input.send_bytes(b"\x1b")

        application = question.application
        application.input = pipe_input
        application.output = DummyOutput()

        result = application.run()

    assert result is None


def test_select_prompt_arrow_navigation_changes_selection() -> None:
    choices = [Choice("Anthropic", value="anthropic"), Choice("OpenAI", value="openai")]
    question = select("Provider", choices)

    with create_pipe_input() as pipe_input:
        pipe_input.send_bytes(b"\x1b[B")
        pipe_input.send_text("\n")

        application = question.application
        application.input = pipe_input
        application.output = DummyOutput()

        result = application.run()

    assert result == "openai"


def test_checkbox_prompt_registers_tab_navigation() -> None:
    question = checkbox(
        "Integrations", [Choice("Grafana", value="grafana"), Choice("Slack", value="slack")]
    )
    key_bindings = question.application.key_bindings
    assert key_bindings is not None
    bindings = {binding.keys for binding in key_bindings.bindings}

    assert (Keys.ControlI,) in bindings
    assert (Keys.BackTab,) in bindings
    assert (Keys.Right,) in bindings
    assert (Keys.Left,) in bindings


def test_checkbox_prompt_escape_cancels() -> None:
    choices = [Choice("Grafana", value="grafana"), Choice("Slack", value="slack")]
    question = checkbox("Integrations", choices)

    with create_pipe_input() as pipe_input:
        pipe_input.send_bytes(b"\x1b")

        application = question.application
        application.input = pipe_input
        application.output = DummyOutput()

        result = application.run()

    assert result is None


def test_checkbox_prompt_space_toggles_current_choice() -> None:
    choices = [Choice("Grafana", value="grafana"), Choice("Slack", value="slack")]
    question = checkbox("Integrations", choices)

    with create_pipe_input() as pipe_input:
        pipe_input.send_text(" \n")

        application = question.application
        application.input = pipe_input
        application.output = DummyOutput()

        result = application.run()

    assert result == ["grafana"]
