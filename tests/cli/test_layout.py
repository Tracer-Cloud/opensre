from __future__ import annotations

import click

from app.cli.__main__ import cli
from app.cli.layout import (
    _HELP_COMMANDS,
    _LANDING_COMMANDS,
    _SHORT_OPTIONS,
    RichGroup,
    render_help,
    render_landing,
)


def _normalized_output(output: str) -> str:
    return " ".join(output.split())


def test_render_help_shows_root_commands(capsys) -> None:
    render_help()
    output = _normalized_output(capsys.readouterr().out)

    assert "Usage: opensre [OPTIONS] COMMAND [ARGS]..." in output
    assert "Commands:" in output
    assert "Options:" in output

    for label, description in _HELP_COMMANDS:
        assert label in output
        assert description in output

    for label, description in _SHORT_OPTIONS:
        assert label in output
        assert description in output


def test_render_landing_shows_root_commands_and_header(capsys) -> None:
    render_landing()
    output = _normalized_output(capsys.readouterr().out)

    assert (
        "open-source SRE agent for automated incident investigation and root cause analysis"
        in output
    )
    assert "Usage: opensre [OPTIONS] COMMAND [ARGS]..." in output
    assert "Quick start:" in output
    assert "Options:" in output

    for label, description in _LANDING_COMMANDS:
        assert label in output
        assert description in output

    for label, description in _SHORT_OPTIONS:
        assert label in output
        assert description in output


def test_help_command_names_match_layout_metadata() -> None:
    assert tuple(cli.commands.keys()) == tuple(label for label, _description in _HELP_COMMANDS)


def test_rich_group_format_help_delegates_to_render_help(monkeypatch) -> None:
    called = []

    def fake_render_help() -> None:
        called.append(True)

    monkeypatch.setattr("app.cli.layout.render_help", fake_render_help)

    group = RichGroup(name="opensre")
    group.format_help(click.Context(group), click.HelpFormatter())

    assert called == [True]
