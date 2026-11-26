"""MCP bridge: configured servers become typed, gated agent tools."""

from __future__ import annotations

import io
import json
import re
import sys
import time
from pathlib import Path

import pytest
from pydantic import ValidationError
from rich.console import Console

from config.constants.repl_autonomy import AutoLevel
from core.agent_harness.tools.action_tools import get_action_tools_from_integrations_view
from core.agent_harness.tools.tool_context import ActionToolScope
from core.llm.shared.tool_schema_normalize import build_openai_tool_specs
from core.llm.types import ToolCall
from core.tool import RegisteredTool, SideEffectLevel
from core.tool.execution import execute_tool_calls
from infrastructure.harness_providers.integration_resolution import resolve_integrations
from integrations.mcp_servers import (
    McpServerConfig,
    bridged_tool_name,
    build_bridged_tool,
    clear_discovery_cache,
    configured_servers,
    mcp_server_tools,
)
from integrations.mcp_servers.discovery import McpServerTool
from integrations.mcp_servers.schema import bridged_input_schema
from surfaces.interactive_shell.runtime.approval_hooks import with_shell_approval
from surfaces.interactive_shell.session import Session

_PROVIDER_TOOL_NAME = re.compile(r"[a-zA-Z0-9_-]{1,64}")
_OPS_SERVER = Path(__file__).resolve().parents[2] / "fixtures" / "mcp" / "ops_server.py"
_PAGED_SERVER = _OPS_SERVER.with_name("paged_server.py")


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

    assert bridged_tool_name("ops", "query_range") == "mcp__ops__query_range"
    assert len(long_a) == len(long_b) == 64
    assert long_a != long_b


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (("ops", "get.logs"), ("ops", "get_logs")),
        (("prod__east", "read"), ("prod", "east__read")),
        (("prod_", "x"), ("prod", "_x")),
        (("my server", "read"), ("my_server", "read")),
    ],
)
def test_distinct_server_tool_pairs_never_share_a_bridged_name(
    first: tuple[str, str], second: tuple[str, str]
) -> None:
    names = {bridged_tool_name(*first), bridged_tool_name(*second)}

    assert len(names) == 2
    assert all(_PROVIDER_TOOL_NAME.fullmatch(name) for name in names)


def test_a_second_server_reusing_a_name_is_skipped(tmp_path: Path) -> None:
    first = McpServerConfig(mode="stdio", command=sys.executable, args=(str(_OPS_SERVER),))
    second = McpServerConfig(mode="stdio", command=str(tmp_path / "other-server"))
    resolved = {
        "_all_mcp_instances": [
            {"name": "ops", "config": first},
            {"name": "ops", "config": second},
        ]
    }

    servers = configured_servers(resolved)
    tools = mcp_server_tools(resolved)

    assert servers == [("ops", first)]
    assert {tool.name for tool in tools} == {"mcp__ops__query_range", "mcp__ops__silence_alert"}


def test_a_malformed_allowlist_is_rejected_not_widened_to_every_tool() -> None:
    with pytest.raises(ValidationError):
        McpServerConfig(url="http://x", allowed_tools={"query_range": True})


def test_a_server_that_stalls_during_startup_cannot_hang_a_call(tmp_path: Path) -> None:
    # Arrange: discovery once succeeded, but the server now never answers initialize.
    stalled = tmp_path / "stalled_server.py"
    stalled.write_text("import time\ntime.sleep(120)\n")
    config = McpServerConfig(
        mode="stdio", command=sys.executable, args=(str(stalled),), timeout_seconds=1
    )
    tool = build_bridged_tool(
        "slow",
        config,
        McpServerTool(
            server="slow", name="read", description="read", input_schema={}, read_only_hint=True
        ),
    )

    # Act
    started = time.monotonic()
    result = tool.run()
    elapsed = time.monotonic() - started

    # Assert
    assert elapsed < 15
    assert result.get("available") is False


def test_every_page_of_tools_is_bridged_with_referenced_arguments_intact() -> None:
    # Arrange: a server that pages tools/list and uses $defs/$ref in a schema.
    config = McpServerConfig(mode="stdio", command=sys.executable, args=(str(_PAGED_SERVER),))
    resolved = {"_all_mcp_instances": [{"name": "logs", "config": config}]}

    # Act: bridge, then build the specs the OpenAI and LiteLLM routes send.
    tools = mcp_server_tools(resolved)
    specs = {spec["function"]["name"]: spec for spec in build_openai_tool_specs(tools)}

    # Assert
    assert set(specs) == {"mcp__logs__ping", "mcp__logs__search"}
    query = specs["mcp__logs__search"]["function"]["parameters"]["properties"]["query"]
    assert query["required"] == ["text"]
    assert set(query["properties"]) == {"text", "level"}
    assert query["properties"]["level"]["enum"] == ["info", "error"]


def test_a_recursive_argument_schema_is_inlined_without_looping() -> None:
    schema = {
        "type": "object",
        "properties": {"node": {"$ref": "#/$defs/Node"}},
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {"child": {"$ref": "#/$defs/Node"}, "name": {"type": "string"}},
            }
        },
    }

    inlined = bridged_input_schema(schema)

    node = inlined["properties"]["node"]
    assert set(node["properties"]) == {"child", "name"}
    assert "$ref" not in json.dumps(inlined) and "$defs" not in inlined


_WINDOW = {
    "type": "object",
    "properties": {"from": {"type": "string", "format": "date-time"}},
    "required": ["from"],
}


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("#/properties/incident", _WINDOW),
        ("#/properties/history/items", _WINDOW),
        ("#/properties/windows/anyOf/0", _WINDOW),
        # RFC 6901 section 6: the fragment is percent-decoded before splitting.
        ("#/properties%2Fincident", _WINDOW),
        ("#%2Fproperties%2Fincident", _WINDOW),
        ("#/properties/from~1to", _WINDOW),
        # Not array indexes int() accepts: left unresolved rather than raising.
        ("#/properties/windows/anyOf/\u00b2", {}),
        ("#/properties/windows/anyOf/" + "0" * 4301, {}),
    ],
)
def test_an_argument_referencing_another_argument_keeps_its_schema(
    ref: str, expected: dict[str, object]
) -> None:
    # The MCP TypeScript SDK (zod v3 path, zod-to-json-schema) emits a reused
    # argument schema as a JSON pointer to its first occurrence, not a $defs entry.
    schema = {
        "type": "object",
        "properties": {
            "incident": _WINDOW,
            "history": {"type": "array", "items": _WINDOW},
            "windows": {"anyOf": [_WINDOW]},
            "from/to": _WINDOW,
            "baseline": {"$ref": ref},
        },
        "required": ["incident", "baseline"],
    }

    inlined = bridged_input_schema(schema)

    assert inlined["properties"]["baseline"] == expected


def test_arguments_named_like_schema_keywords_are_kept() -> None:
    schema = {
        "type": "object",
        "properties": {
            "definitions": {"type": "string"},
            "$ref": {"type": "string"},
            "$defs": {"type": "integer"},
            "payload": {"type": "object", "default": {"$ref": "kept as data"}},
        },
        "required": ["definitions", "$ref"],
        "additionalProperties": False,
    }

    inlined = bridged_input_schema(schema)

    assert set(inlined["properties"]) == {"definitions", "$ref", "$defs", "payload"}
    assert inlined["properties"]["payload"]["default"] == {"$ref": "kept as data"}
    assert inlined["required"] == ["definitions", "$ref"]


def test_a_schema_that_would_expand_exponentially_stays_bounded() -> None:
    # Eight levels, each referencing the next ten times: ~10^8 nodes if fully inlined.
    defs = {
        f"L{level}": {
            "type": "object",
            "properties": {f"p{i}": {"$ref": f"#/$defs/L{level + 1}"} for i in range(10)},
        }
        for level in range(8)
    }
    defs["L8"] = {"type": "string"}
    schema = {"type": "object", "properties": {"root": {"$ref": "#/$defs/L0"}}, "$defs": defs}

    started = time.monotonic()
    inlined = bridged_input_schema(schema)
    elapsed = time.monotonic() - started

    text = json.dumps(inlined)
    assert elapsed < 2
    assert len(text) < 1_000_000
    assert "$ref" not in text and "root" in inlined["properties"]
