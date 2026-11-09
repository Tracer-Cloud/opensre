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


def _request(tool_name: str, arguments: dict[str, object] | None = None) -> ToolExecutionRequest:
    clear_tool_registry_cache()
    return ToolExecutionRequest(
        tool_call=ToolCall(id="call-1", name=tool_name, input=dict(arguments or {})),
        tool=get_registered_tool_map()[tool_name],
        arguments=dict(arguments or {}),
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


def test_generated_code_asks_at_every_auto_level() -> None:
    assert frozenset({"execute_python_code"}) == ASK_AT_EVERY_AUTO_LEVEL_TOOL_NAMES


def test_generated_code_allowed_for_the_session_stops_asking_until_new() -> None:
    # Arrange
    session = Session()
    console, _printed = _console()
    answers = ["allow_session", "n", "y"]
    offered: list[tuple[tuple[str, str], ...] | None] = []

    def confirm(_prompt: str) -> str:
        offered.append(session.terminal.pending_confirm_options)
        return answers.pop(0)

    hooks = with_shell_approval(
        None, session=session, console=console, confirm_fn=confirm, is_tty=True
    )
    assert hooks.before_tool_call is not None
    offline = _request("execute_python_code", {"code": "print(1)"})
    networked = _request("execute_python_code", {"code": "print(1)", "allow_network": True})

    # Act
    first = hooks.before_tool_call(offline)
    second = hooks.before_tool_call(offline)
    with_network = hooks.before_tool_call(networked)
    session.clear()
    after_new = hooks.before_tool_call(offline)

    # Assert
    assert first == BeforeToolCallResult(approved=True)
    assert second == BeforeToolCallResult(approved=True)
    assert with_network is not None and with_network.blocked
    assert after_new == BeforeToolCallResult(approved=True)
    # Asked for the first call, the network call, and again after /new.
    assert len(offered) == 3
    first_rows, network_rows, after_new_rows = offered
    assert first_rows is not None and network_rows is not None
    assert ("allow_session", "Yes, allow for this session") in first_rows
    assert all(answer != "allow_session" for answer, _label in network_rows)
    assert after_new_rows == first_rows
