"""MCP bridge: configured servers become typed, gated agent tools."""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import pytest
from rich.console import Console

from config.constants.repl_autonomy import AutoLevel
from core.agent_harness.tools.action_tools import get_action_tools_from_integrations_view
from core.agent_harness.tools.tool_context import ActionToolScope
from core.llm.types import ToolCall
from core.tool import RegisteredTool, SideEffectLevel
from core.tool.execution import execute_tool_calls
from infrastructure.harness_providers.integration_resolution import resolve_integrations
from integrations.mcp_servers import (
    McpServerConfig,
    bridged_tool_name,
    clear_discovery_cache,
    mcp_server_tools,
)
from surfaces.interactive_shell.runtime.approval_hooks import with_shell_approval
from surfaces.interactive_shell.session import Session

_OPS_SERVER = Path(__file__).resolve().parents[2] / "fixtures" / "mcp" / "ops_server.py"


@pytest.fixture(autouse=True)
def _fresh_discovery(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clear_discovery_cache()
    # Env integrations are read only when the local store is empty.
    monkeypatch.setattr("integrations.store.STORE_PATH", tmp_path / "integrations.json")


def _configure_ops_server(monkeypatch: pytest.MonkeyPatch, log: Path) -> None:
    monkeypatch.setenv("OPS_SERVER_LOG", str(log))
    monkeypatch.setenv(
        "MCP_INSTANCES",
        json.dumps(
            [
                {
                    "name": "ops",
                    "mode": "stdio",
                    "command": sys.executable,
                    "args": [str(_OPS_SERVER)],
                }
            ]
        ),
    )


def _call(
    tools: list[RegisteredTool],
    resolved: dict[str, object],
    session: Session,
    name: str,
    arguments: dict[str, object],
    *,
    answer: str = "y",
) -> tuple[bool, bool]:
    """Run one call through the shell approval hook; return (asked, is_error)."""
    asked: list[str] = []

    def confirm(prompt: str) -> str:
        asked.append(prompt)
        return answer

    hooks = with_shell_approval(
        None, session=session, console=Console(file=io.StringIO()), confirm_fn=confirm, is_tty=True
    )
    [result] = execute_tool_calls(
        [ToolCall(id=name, name=name, input=arguments)], tools, resolved, hooks=hooks
    )
    return bool(asked), result.is_error


def test_configured_server_tools_are_typed_gated_and_reach_the_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange
    log = tmp_path / "calls.log"
    log.touch()
    _configure_ops_server(monkeypatch, log)
    resolved = resolve_integrations()
    session = Session()
    session.terminal.auto_level = AutoLevel.MED
    scope = ActionToolScope(session=session, console=Console(file=io.StringIO()))

    # Act
    tools = get_action_tools_from_integrations_view(scope, resolved_integrations=resolved)
    bridged = {tool.name: tool for tool in tools if tool.name.startswith("mcp__")}
    read_asked, read_failed = _call(
        tools,
        resolved,
        session,
        "mcp__ops__query_range",
        {"promql": "up", "minutes": 60, "user": "alice"},
    )
    declined_asked, declined_failed = _call(
        tools,
        resolved,
        session,
        "mcp__ops__silence_alert",
        {"alert_id": "A1", "reason": "x"},
        answer="n",
    )

    # Assert
    assert set(bridged) == {"mcp__ops__query_range", "mcp__ops__silence_alert"}
    read_tool = bridged["mcp__ops__query_range"]
    assert read_tool.side_effect_level is SideEffectLevel.READ_ONLY
    assert bridged["mcp__ops__silence_alert"].side_effect_level is SideEffectLevel.EXTERNAL
    # The server's own argument names survive, including ones that are
    # config-only for built-in tools.
    assert set(read_tool.public_input_schema["properties"]) == {"promql", "minutes", "user"}
    assert read_tool.public_input_schema["required"] == ["promql"]
    assert (read_asked, read_failed) == (False, False)
    assert (declined_asked, declined_failed) == (True, True)
    assert log.read_text().splitlines() == ["query_range:up:60:alice"]


def test_unreachable_server_contributes_no_tools_and_is_not_retried_each_turn(
    tmp_path: Path,
) -> None:
    # Arrange
    missing = McpServerConfig(mode="stdio", command=str(tmp_path / "no-such-server"))
    resolved = {"_all_mcp_instances": [{"name": "down", "config": missing}]}

    # Act
    started = time.monotonic()
    first = mcp_server_tools(resolved)
    first_seconds = time.monotonic() - started
    started = time.monotonic()
    second = mcp_server_tools(resolved)
    second_seconds = time.monotonic() - started

    # Assert
    assert first == () and second == ()
    assert first_seconds < 15
    assert second_seconds < 0.5


@pytest.mark.parametrize(
    ("config", "tool", "read_only_hint", "exposed"),
    [
        (McpServerConfig(url="http://x"), "anything", False, True),
        (McpServerConfig(url="http://x", read_only=True), "write", False, False),
        (McpServerConfig(url="http://x", read_only=True), "read", True, True),
        (McpServerConfig(url="http://x", allowed_tools=("read",)), "other", True, False),
    ],
)
def test_server_config_limits_which_tools_are_exposed(
    config: McpServerConfig, tool: str, read_only_hint: bool, exposed: bool
) -> None:
    assert config.exposes(tool, read_only_hint=read_only_hint) is exposed


def test_bridged_names_are_provider_safe_and_stay_distinct_when_capped() -> None:
    long_a = bridged_tool_name("grafana.cloud", "x" * 80 + "a")
    long_b = bridged_tool_name("grafana.cloud", "x" * 80 + "b")

    assert bridged_tool_name("my server", "get-logs") == "mcp__my_server__get-logs"
    assert len(long_a) == len(long_b) == 64
    assert long_a != long_b
