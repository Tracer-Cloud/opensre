"""Sentry and PostHog MCP connections reach the agent as typed bridged tools."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
from rich.console import Console

from core.agent_harness.tools.action_tools import get_action_tools_from_integrations_view
from core.agent_harness.tools.tool_context import ActionToolScope
from core.llm.types import ToolCall
from core.tool import SideEffectLevel
from core.tool.execution import execute_tool_calls
from infrastructure.harness_providers.integration_resolution import resolve_integrations
from integrations.mcp_servers import clear_discovery_cache
from surfaces.interactive_shell.session import Session

_ENV_SERVER = Path(__file__).resolve().parents[2] / "fixtures" / "mcp" / "env_server.py"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clear_discovery_cache()
    # Env integrations are read only when the local store is empty.
    monkeypatch.setattr("integrations.store.STORE_PATH", tmp_path / "integrations.json")
    for name in ("SENTRY_MCP", "POSTHOG_MCP"):
        for suffix in ("_MODE", "_URL", "_COMMAND", "_ARGS", "_AUTH_TOKEN"):
            monkeypatch.delenv(name + suffix, raising=False)


@pytest.mark.parametrize(
    ("prefix", "server", "credential_env"),
    [
        ("SENTRY_MCP", "sentry", "SENTRY_ACCESS_TOKEN"),
        ("POSTHOG_MCP", "posthog", "POSTHOG_PERSONAL_API_KEY"),
    ],
)
def test_vendor_mcp_config_becomes_typed_gated_tools_with_its_credentials(
    monkeypatch: pytest.MonkeyPatch, prefix: str, server: str, credential_env: str
) -> None:
    # Arrange: the vendor's existing env config, pointing at a local stdio server.
    monkeypatch.setenv(f"{prefix}_MODE", "stdio")
    monkeypatch.setenv(f"{prefix}_COMMAND", sys.executable)
    monkeypatch.setenv(f"{prefix}_ARGS", str(_ENV_SERVER))
    monkeypatch.setenv(f"{prefix}_AUTH_TOKEN", f"{server}-token")
    resolved = resolve_integrations()
    scope = ActionToolScope(session=Session(), console=Console(file=io.StringIO()))

    # Act
    tools = get_action_tools_from_integrations_view(scope, resolved_integrations=resolved)
    bridged = {tool.name: tool for tool in tools if tool.name.startswith(f"mcp__{server}__")}
    read_name = f"mcp__{server}__read_env"
    [result] = execute_tool_calls(
        [ToolCall(id="1", name=read_name, input={"name": credential_env})], tools, resolved
    )

    # Assert: typed tools, gated by the server's annotations, with the token delivered.
    assert set(bridged) == {read_name, f"mcp__{server}__acknowledge"}
    assert bridged[read_name].side_effect_level is SideEffectLevel.READ_ONLY
    assert bridged[f"mcp__{server}__acknowledge"].side_effect_level is SideEffectLevel.EXTERNAL
    assert set(bridged[read_name].public_input_schema["properties"]) == {"name"}
    assert not result.is_error
    assert f"{server}-token" in json.dumps(result.content, default=str)
