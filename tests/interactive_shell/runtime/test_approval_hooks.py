"""Tests for the interactive shell's tool approval hook."""

from __future__ import annotations

import io
import json
import os
import re
from dataclasses import replace
from pathlib import Path

import pytest
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
from tools.interactive_shell.actions.shell import shell_run_tool
from tools.registry import clear_tool_registry_cache, get_registered_tool_map


def _request(tool_name: str, arguments: dict[str, object] | None = None) -> ToolExecutionRequest:
    clear_tool_registry_cache()
    resolved_arguments = arguments or {}
    return ToolExecutionRequest(
        tool_call=ToolCall(id="call-1", name=tool_name, input=resolved_arguments),
        tool=get_registered_tool_map()[tool_name],
        arguments=resolved_arguments,
        source="test",
        resolved_integrations={},
    )


def _console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(file=buffer, force_terminal=False, width=10000), buffer


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


def test_mcp_mutations_prompt_at_default_auto_level() -> None:
    # Arrange
    session = Session()
    console, printed = _console()
    asked: list[str] = []

    def confirm(prompt: str) -> str:
        asked.append(prompt)
        return "y"

    hooks = with_shell_approval(
        None, session=session, console=console, confirm_fn=confirm, is_tty=True
    )
    assert hooks.before_tool_call is not None

    # Act
    decision = hooks.before_tool_call(
        _request(
            "call_mcp_gateway_tool",
            {
                "tool_name": "restart_service",
                "arguments": {"service": "prod", "api_token": "sample-token"},
            },
        )
    )

    # Assert
    assert session.terminal.auto_level == DEFAULT_AUTO_LEVEL == AutoLevel.HIGH
    assert decision == BeforeToolCallResult(approved=True)
    assert len(asked) == 1
    assert "restart_service" in printed.getvalue()
    assert '"service": "prod"' in printed.getvalue()
    assert "sample-token" not in printed.getvalue()


def test_mcp_mutations_still_prompt_in_trust_mode() -> None:
    session = Session()
    session.terminal.trust_mode = True
    console, _printed = _console()
    asked: list[str] = []

    def confirm(prompt: str) -> str:
        asked.append(prompt)
        return "y"

    hooks = with_shell_approval(
        None, session=session, console=console, confirm_fn=confirm, is_tty=True
    )
    assert hooks.before_tool_call is not None

    decision = hooks.before_tool_call(
        _request(
            "call_mcp_gateway_tool",
            {"tool_name": "restart_service", "arguments": {"service": "prod"}},
        )
    )

    assert decision == BeforeToolCallResult(approved=True)
    assert len(asked) == 1


def test_generated_code_asks_at_every_auto_level() -> None:
    assert {"execute_python_code", "call_mcp_gateway_tool"} <= ASK_AT_EVERY_AUTO_LEVEL_TOOL_NAMES


@pytest.mark.parametrize(
    ("credential", "secrets"),
    [
        ({"private_key": "opaque-private-value"}, ("opaque-private-value",)),
        (
            {
                "items": [
                    {
                        "header": "Bearer sample-token",
                        "value": "sk-testcredentialtestcredential",
                    }
                ]
            },
            ("sample-token", "sk-testcredentialtestcredential"),
        ),
        (
            {"body": "-----BEGIN PRIVATE KEY-----\nkey-material\n-----END PRIVATE KEY-----"},
            ("key-material",),
        ),
    ],
)
def test_approval_preview_scrubs_nested_credentials(
    credential: dict[str, object], secrets: tuple[str, ...]
) -> None:
    console, printed = _console()
    payload = {"service": "prod", **credential}
    arguments: dict[str, object] = {"tool_name": "restart_service", "arguments": payload}
    hooks = with_shell_approval(
        None, session=Session(), console=console, confirm_fn=lambda _prompt: "y", is_tty=True
    )
    assert hooks.before_tool_call is not None

    decision = hooks.before_tool_call(_request("call_mcp_gateway_tool", arguments))

    assert decision == BeforeToolCallResult(approved=True)
    preview = printed.getvalue()
    assert "restart_service" in preview and '"service": "prod"' in preview
    assert "REDACTED" in preview or "redacted" in preview
    for secret in secrets:
        assert secret not in preview
    assert payload == {"service": "prod", **credential}


def test_approval_keeps_later_mutation_targets_visible() -> None:
    console, printed = _console()
    hooks = with_shell_approval(
        None, session=Session(), console=console, confirm_fn=lambda _prompt: "y", is_tty=True
    )
    assert hooks.before_tool_call is not None
    decision = hooks.before_tool_call(
        _request(
            "call_mcp_gateway_tool",
            {
                "tool_name": "restart_service",
                "arguments": {"padding": "x" * 300, "service": "prod"},
            },
        )
    )
    assert decision == BeforeToolCallResult(approved=True)
    assert '"service": "prod"' in " ".join(printed.getvalue().split())


@pytest.mark.parametrize("tool_name", ["call_mcp_gateway_tool", "execute_python_code"])
def test_large_arguments_can_reach_operator_confirmation(tool_name: str) -> None:
    console, printed = _console()
    asked: list[str] = []

    def confirm(prompt: str) -> str:
        asked.append(prompt)
        path_match = re.search(r"before approving: (.+arguments.json)", printed.getvalue())
        assert path_match is not None
        review_path = Path(path_match.group(1))
        complete = json.loads(review_path.read_text())
        assert complete == arguments
        if os.name == "posix":
            assert review_path.stat().st_mode & 0o777 == 0o600
        return "y"

    hooks = with_shell_approval(
        None, session=Session(), console=console, confirm_fn=confirm, is_tty=True
    )
    assert hooks.before_tool_call is not None
    arguments: dict[str, object] = (
        {"arguments": {"padding": "x" * 5000, "service": "prod"}}
        if tool_name == "call_mcp_gateway_tool"
        else {"code": "print('hello')\n" * 500}
    )
    decision = hooks.before_tool_call(_request(tool_name, arguments))
    assert decision == BeforeToolCallResult(approved=True)
    assert len(asked) == 1
    shown = " ".join(printed.getvalue().split())
    assert "[truncated]" in shown
    assert len(shown) < 4500
    if tool_name == "call_mcp_gateway_tool":
        assert '"service": "prod"' in shown
    review_files = re.findall(r"before approving: (.+arguments.json)", printed.getvalue())
    assert all(not Path(path).exists() for path in review_files)


@pytest.mark.parametrize("is_tty", [True, False])
def test_shell_action_approval_metadata_keeps_existing_auto_policy(is_tty: bool) -> None:
    console, printed = _console()
    asked: list[str] = []
    passed: list[str] = []

    def confirm(prompt: str) -> str:
        asked.append(prompt)
        return "n"

    def later(request: ToolExecutionRequest) -> None:
        passed.append(request.tool_call.name)

    hooks = with_shell_approval(
        ToolExecutionHooks(before_tool_call=later),
        session=Session(),
        console=console,
        confirm_fn=confirm,
        is_tty=is_tty,
    )
    request = replace(
        _request("start_hosted_gateway"),
        tool=shell_run_tool,
        tool_call=ToolCall(id="shell-1", name="shell_run", input={"command": "echo hello"}),
        arguments={"command": "echo hello"},
    )
    assert shell_run_tool.requires_approval
    assert hooks.before_tool_call is not None
    assert hooks.before_tool_call(request) is None
    assert passed == ["shell_run"]
    assert asked == [] and printed.getvalue() == ""


def test_approval_does_not_authorize_hidden_argument_fields() -> None:
    console, _printed = _console()
    asked: list[str] = []

    def confirm(prompt: str) -> str:
        asked.append(prompt)
        return "y"

    hooks = with_shell_approval(
        None, session=Session(), console=console, confirm_fn=confirm, is_tty=True
    )
    assert hooks.before_tool_call is not None
    decision = hooks.before_tool_call(
        _request("call_mcp_gateway_tool", {"arguments": {f"field_{i}": i for i in range(1000)}})
    )
    assert decision is not None and decision.blocked and not decision.approved
    assert "fields" in decision.reason
    assert asked == []
