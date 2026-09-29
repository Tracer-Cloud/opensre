"""Tests for the shell's approval hook: no tool asks at the default auto level."""

from __future__ import annotations

import io

from rich.console import Console

from config.constants.repl_autonomy import (
    ASK_AT_EVERY_AUTO_LEVEL_TOOL_NAMES,
    DEFAULT_AUTO_LEVEL,
    AutoLevel,
)
from core.llm.types import ToolCall
from core.tool import BeforeToolCallResult, ToolExecutionHooks, ToolExecutionRequest
from surfaces.interactive_shell.runtime.approval_hooks import with_shell_approval
from surfaces.interactive_shell.session import Session
from tools.registry import clear_tool_registry_cache, get_registered_tool_map


def _request(tool_name: str) -> ToolExecutionRequest:
    clear_tool_registry_cache()
    return ToolExecutionRequest(
        tool_call=ToolCall(id="call-1", name=tool_name, input={}),
        tool=get_registered_tool_map()[tool_name],
        arguments={},
        source="test",
        resolved_integrations={},
    )


def _console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(file=buffer, force_terminal=False), buffer


def test_stop_is_not_asked_at_the_default_allow_all_level() -> None:
    # Arrange
    session = Session()
    console, printed = _console()
    inner_calls: list[str] = []

    def inner(request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        inner_calls.append(request.tool_call.name)
        return None

    hooks = with_shell_approval(
        ToolExecutionHooks(before_tool_call=inner),
        session=session,
        console=console,
        confirm_fn=lambda _prompt: "n",
        is_tty=True,
    )
    assert hooks.before_tool_call is not None

    # Act
    decision = hooks.before_tool_call(_request("stop_hosted_gateway"))

    # Assert
    assert session.terminal.auto_level == DEFAULT_AUTO_LEVEL == AutoLevel.HIGH
    assert decision is None
    assert printed.getvalue() == ""
    assert inner_calls == ["stop_hosted_gateway"]


def test_other_tools_are_not_asked_about() -> None:
    # Arrange
    console, printed = _console()
    asked: list[str] = []

    def confirm(prompt: str) -> str:
        asked.append(prompt)
        return "y"

    hooks = with_shell_approval(
        None, session=Session(), console=console, confirm_fn=confirm, is_tty=True
    )
    assert hooks.before_tool_call is not None

    # Act
    decision = hooks.before_tool_call(_request("start_hosted_gateway"))

    # Assert
    assert decision is None
    assert asked == [] and printed.getvalue() == ""


def test_no_tool_asks_at_every_auto_level() -> None:
    assert not ASK_AT_EVERY_AUTO_LEVEL_TOOL_NAMES
