"""Tests for slash typo suggestion helpers."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from surfaces.interactive_shell.command_registry import SLASH_COMMANDS, dispatch_slash
from surfaces.interactive_shell.command_registry.suggestions import (
    format_invalid_subcommand_message,
    format_unknown_slash_message,
    resolve_literal_slash_typo,
    subcommand_hints,
)
from surfaces.interactive_shell.runtime.action_turn import run_action_tool_turn
from surfaces.interactive_shell.session import Session


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def test_format_unknown_slash_message_without_suggestion_points_to_help() -> None:
    message = format_unknown_slash_message(
        "/made-up",
        command_names=tuple(SLASH_COMMANDS),
    )
    assert message == "Unknown command: /made-up. Type /help for the full command list."


def test_format_unknown_slash_message_with_suggestion() -> None:
    message = format_unknown_slash_message(
        "/modle",
        command_names=tuple(SLASH_COMMANDS),
    )
    assert "Did you mean /model?" in message
    assert "Type /help" in message


def test_resolve_literal_slash_typo_unknown_root() -> None:
    typo = resolve_literal_slash_typo("/modl", SLASH_COMMANDS)
    assert typo is not None
    assert typo.outcome == "unknown_command"
    assert "Did you mean /model?" in typo.message


@pytest.mark.parametrize(
    "command_line",
    [
        "/resume redis",
        "/help model",
        "/help /model",
        "/integrations ls",
        "/mcp ls",
    ],
)
def test_resolve_literal_slash_typo_allows_free_form_first_args(command_line: str) -> None:
    assert resolve_literal_slash_typo(command_line, SLASH_COMMANDS) is None


def test_tools_argument_hint_points_to_the_direct_browser() -> None:
    typo = resolve_literal_slash_typo("/tools list", SLASH_COMMANDS)
    assert typo is not None
    assert typo.outcome == "invalid_subcommand"
    assert typo.message == "Use /tools without arguments to browse registered tools."


def test_dispatch_invalid_subcommand_is_handled_by_command_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.integrations.repl_data.load_verified_integrations",
        lambda: [],
    )
    session = Session()
    console, buf = _capture()
    assert dispatch_slash("/integrations bogus", session, console) is True
    assert "unknown subcommand" in buf.getvalue().lower()
    assert resolve_literal_slash_typo("/integrations bogus", SLASH_COMMANDS) is None


@pytest.mark.parametrize("command_line", ["/integrations bogus", "/mcp bogus"])
def test_dispatch_unknown_subcommand_records_turn_as_failed(
    monkeypatch: pytest.MonkeyPatch,
    command_line: str,
) -> None:
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.integrations.repl_data.load_verified_integrations",
        lambda: [],
    )
    session = Session()
    console, buf = _capture()
    assert dispatch_slash(command_line, session, console, is_tty=False) is True
    assert "unknown subcommand" in buf.getvalue().lower()
    assert session.history[-1]["ok"] is False


def test_subcommand_hints_ignores_usage_placeholders() -> None:
    resume = SLASH_COMMANDS["/resume"]
    assert subcommand_hints(resume) == ()
    help_cmd = SLASH_COMMANDS["/help"]
    assert subcommand_hints(help_cmd) == ()


def test_format_invalid_subcommand_message_lists_known_subcommands() -> None:
    cmd = SLASH_COMMANDS["/integrations"]
    message = format_invalid_subcommand_message(cmd, ["bogus"])
    assert "Invalid subcommand: bogus" in message
    assert "/integrations list" in message


def test_dispatch_unknown_command_records_full_response_and_outcome() -> None:
    session = Session()
    console, buf = _capture()
    assert dispatch_slash("/modle", session, console) is True
    output = buf.getvalue()
    assert "Unknown command" in output
    latest = session.history[-1]
    assert latest["ok"] is False
    assert latest["slash_outcome"] == "unknown_command"
    assert latest["response_text"] == latest["response_text"].strip()
    assert "Type /help" in latest["response_text"]


def test_run_action_tool_turn_handles_unknown_literal_slash_before_tool_validation() -> None:
    session = Session()
    console, buf = _capture()
    result = run_action_tool_turn("/invest", session, console)
    assert result.handled is True
    assert result.response_text
    assert "Unknown command" in result.response_text
    assert "Unknown command" in buf.getvalue()
    latest = session.history[-1]
    assert latest["type"] == "slash"
    assert latest["slash_outcome"] == "unknown_command"
    assert latest["response_text"] == result.response_text
