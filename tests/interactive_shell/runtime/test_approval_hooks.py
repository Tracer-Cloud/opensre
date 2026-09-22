"""Tests for the shell's approval hook: organization-wide tools ask at every ``/auto`` level."""

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


def test_stop_asks_at_the_default_allow_all_level_and_a_no_blocks_the_call() -> None:
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

    # Assert: asked although the level allows everything, and nothing ran after the refusal.
    assert session.terminal.auto_level == DEFAULT_AUTO_LEVEL == AutoLevel.HIGH
    assert decision is not None and decision.blocked is True
    assert "declined stop_hosted_gateway" in decision.reason
    assert "for the whole organization" in printed.getvalue()
    assert inner_calls == []


def test_an_approved_stop_runs_and_a_later_hook_can_still_block_it() -> None:
    # Arrange
    console, _printed = _console()
    asked: list[str] = []

    def confirm(prompt: str) -> str:
        asked.append(prompt)
        return "y"

    def approve_only(tool_hooks: ToolExecutionHooks | None) -> BeforeToolCallResult | None:
        hooks = with_shell_approval(
            tool_hooks, session=Session(), console=console, confirm_fn=confirm, is_tty=True
        )
        assert hooks.before_tool_call is not None
        return hooks.before_tool_call(_request("stop_hosted_gateway"))

    def blocking(_request: ToolExecutionRequest) -> BeforeToolCallResult | None:
        return BeforeToolCallResult(blocked=True, reason="duplicate call")

    # Act
    alone = approve_only(None)
    chained = approve_only(ToolExecutionHooks(before_tool_call=blocking))

    # Assert
    assert alone is not None and alone.approved is True and alone.blocked is False
    assert chained is not None and chained.blocked is True and chained.reason == "duplicate call"
    assert len(asked) == 2


def test_without_a_terminal_stop_is_blocked_instead_of_running_unasked() -> None:
    # Arrange
    console, _printed = _console()
    hooks = with_shell_approval(
        None, session=Session(), console=console, confirm_fn=lambda _prompt: "y", is_tty=False
    )
    assert hooks.before_tool_call is not None

    # Act
    decision = hooks.before_tool_call(_request("stop_hosted_gateway"))

    # Assert
    assert decision is not None and decision.blocked is True


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


def test_every_always_ask_name_is_a_registered_tool_with_a_reason_to_show() -> None:
    # Arrange
    clear_tool_registry_cache()
    registered = get_registered_tool_map()

    # Act
    missing = sorted(name for name in ASK_AT_EVERY_AUTO_LEVEL_TOOL_NAMES if name not in registered)
    without_reason = sorted(
        name
        for name in ASK_AT_EVERY_AUTO_LEVEL_TOOL_NAMES
        if name in registered and not getattr(registered[name], "approval_reason", "")
    )

    # Assert: a renamed tool must not silently lose its confirmation.
    assert missing == []
    assert without_reason == []
