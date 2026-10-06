"""Unit tests for modular slash-command registry."""

from __future__ import annotations

import io

from rich.console import Console

from surfaces.interactive_shell.command_registry import SLASH_COMMANDS, dispatch_slash
from surfaces.interactive_shell.command_registry.integrations import (
    _INTEGRATIONS_FIRST_ARGS,
    _MCP_FIRST_ARGS,
)
from surfaces.interactive_shell.command_registry.loops_cmds import _LOOPS_FIRST_ARGS
from surfaces.interactive_shell.command_registry.model.command import _MODEL_FIRST_ARGS
from surfaces.interactive_shell.command_registry.settings_cmds import (
    _TRUST_FIRST_ARGS,
    _VERBOSE_FIRST_ARGS,
)
from surfaces.interactive_shell.session import Session


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def test_slash_registry_includes_modular_commands() -> None:
    for name in (
        "/help",
        "/?",
        "/exit",
        "/model",
        "/tools",
        "/integrations",
        "/loops",
        "/tasks",
        "/health",
    ):
        assert name in SLASH_COMMANDS


def test_dispatch_unknown_command_stays_in_repl() -> None:
    session = Session()
    console, buf = _capture()
    assert dispatch_slash("/not-a-real-slash", session, console) is True
    assert "Unknown command" in buf.getvalue()


def test_registry_first_arg_completion_hints_co_located_with_handlers() -> None:
    """Merged registry exposes the same first-arg tab tuples defined in each module."""
    expected: dict[str, tuple[tuple[str, str], ...]] = {
        "/model": _MODEL_FIRST_ARGS,
        "/tools": (),
        "/integrations": _INTEGRATIONS_FIRST_ARGS,
        "/mcp": _MCP_FIRST_ARGS,
        "/trust": _TRUST_FIRST_ARGS,
        "/verbose": _VERBOSE_FIRST_ARGS,
        "/loops": _LOOPS_FIRST_ARGS,
    }
    for name, tup in expected.items():
        assert SLASH_COMMANDS[name].first_arg_completions == tup

    assert SLASH_COMMANDS["/help"].first_arg_completions == ()


def test_exit_and_quit_are_non_mutating() -> None:
    # Control commands must be declared non-mutating so the execution gate skips them.
    assert SLASH_COMMANDS["/exit"].mutating is False
    assert SLASH_COMMANDS["/quit"].mutating is False


def test_plan_only_gate_does_not_block_exit() -> None:
    # A standing plan-only request must never stop the user from leaving the shell:
    # /exit is non-mutating, so it runs without a confirmation prompt.
    console, _ = _capture()
    session = Session()
    session.plan_only_until_authorized = True
    prompted = {"asked": False}

    def _confirm(_prompt: str) -> str:
        prompted["asked"] = True
        return "n"

    result = dispatch_slash("/exit", session, console, confirm_fn=_confirm, is_tty=True)

    assert prompted["asked"] is False
    assert result is False  # the REPL should exit
