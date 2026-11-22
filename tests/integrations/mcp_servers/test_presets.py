"""Official MCP server presets, and stdio environments, reach the real server."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

from infrastructure.harness_providers.integration_resolution import resolve_integrations
from integrations.mcp_servers import (
    McpServerConfig,
    clear_discovery_cache,
    mcp_server_tools,
    presets,
)
from integrations.mcp_servers.setup import MCP_SERVERS_SETUP
from integrations.setup_flow import apply_setup
from integrations.store import get_integration

_ENV_SERVER = str(Path(__file__).resolve().parents[2] / "fixtures" / "mcp" / "env_server.py")


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clear_discovery_cache()
    monkeypatch.setattr("integrations.store.STORE_PATH", tmp_path / "integrations.json")
    monkeypatch.delenv("MCP_INSTANCES", raising=False)


def _run(tool_name: str, **arguments: object) -> str:
    tools = {tool.name: tool for tool in mcp_server_tools(resolve_integrations())}
    payload = tools[tool_name].run(**arguments)
    return str(payload.get("text") or "")


def test_a_stdio_servers_env_reaches_its_process(monkeypatch: pytest.MonkeyPatch) -> None:
    server = {
        "name": "envsrv",
        "command": sys.executable,
        "args": [_ENV_SERVER],
        "env": {"OPS_API_KEY": "k-123"},
    }
    monkeypatch.setenv("MCP_INSTANCES", json.dumps([server]))

    assert _run("mcp__envsrv__show_env", name="OPS_API_KEY") == "k-123"


def test_a_preset_set_up_end_to_end_gets_its_credentials_through_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: stand the fixture in for `uvx mcp-grafana`, so the real setup,
    # verifier and bridge run end to end without downloading the server.
    grafana = next(p for p in presets.PRESETS if p.key == "grafana")
    fixture = dataclasses.replace(grafana.servers[""], command=sys.executable, args=(_ENV_SERVER,))
    catalog = tuple(
        dataclasses.replace(p, servers={"": fixture}) if p.key == "grafana" else p
        for p in presets.PRESETS
    )
    monkeypatch.setattr(presets, "PRESETS", catalog)

    # Act
    outcome = apply_setup(
        MCP_SERVERS_SETUP,
        {
            "name": "grafana",
            "grafana_url": "https://myorg.grafana.net",
            "grafana_service_account_token": "glsa_secret",
            "read_only": "yes",
        },
    )

    # Assert: credentials arrive as env vars; read-only is enforced server-side.
    assert outcome.ok, outcome.detail
    assert _run("mcp__grafana__show_env", name="GRAFANA_URL") == "https://myorg.grafana.net"
    assert _run("mcp__grafana__show_env", name="GRAFANA_SERVICE_ACCOUNT_TOKEN") == "glsa_secret"
    assert _run("mcp__grafana__show_args") == "--disable-write"
    [instance] = (get_integration("mcp") or {})["instances"]
    assert instance["credentials"]["grafana_url"] == "https://myorg.grafana.net"


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        (
            {"grafana_url": "http://localhost:3000", "grafana_service_account_token": "t"},
            {
                "command": "uvx",
                "args": ("mcp-grafana",),
                "env": {
                    "GRAFANA_URL": "http://localhost:3000",
                    "GRAFANA_SERVICE_ACCOUNT_TOKEN": "t",
                },
            },
        ),
        (
            {"pagerduty_user_api_token": "t", "pagerduty_api_host": "https://api.eu.pagerduty.com"},
            {
                "command": "uvx",
                "args": ("pagerduty-mcp",),
                "env": {
                    "PAGERDUTY_USER_API_KEY": "t",
                    "PAGERDUTY_API_HOST": "https://api.eu.pagerduty.com",
                },
            },
        ),
        (
            {"aws_mcp_server": "ecs", "aws_mcp_region": "eu-west-1", "aws_mcp_profile": "ro"},
            {
                "command": "uvx",
                "args": ("--from", "awslabs-ecs-mcp-server", "ecs-mcp-server"),
                "env": {
                    "AWS_REGION": "eu-west-1",
                    "AWS_PROFILE": "ro",
                    "ALLOW_WRITE": "false",
                    "ALLOW_SENSITIVE_DATA": "false",
                },
            },
        ),
        (
            {"aws_mcp_server": "cloudwatch"},
            {"command": "uvx", "args": ("awslabs.cloudwatch-mcp-server@latest",), "env": {}},
        ),
    ],
)
def test_local_presets_start_the_documented_command_with_credentials_in_env(
    answers: dict[str, str], expected: dict[str, object]
) -> None:
    config = McpServerConfig.from_mapping(answers)

    assert config.mode == "stdio"
    assert (config.command, config.args, config.env) == (
        expected["command"],
        expected["args"],
        expected["env"],
    )


def test_datadog_preset_sends_its_keys_as_headers_to_the_chosen_site() -> None:
    config = McpServerConfig.from_mapping(
        {"datadog_api_key": "api", "datadog_application_key": "app", "datadog_site": "datadoghq.eu"}
    )

    assert (config.mode, config.url) == ("streamable-http", "https://mcp.datadoghq.eu/v1/mcp")
    assert config.request_headers == {"DD_API_KEY": "api", "DD_APPLICATION_KEY": "app"}
    assert config.args == ()


def test_a_datadog_site_outside_the_known_sites_is_refused() -> None:
    # Otherwise the keys could be sent to any host named as the "site".
    with pytest.raises(ValueError, match="must be one of"):
        McpServerConfig.from_mapping(
            {
                "datadog_api_key": "api",
                "datadog_application_key": "app",
                "datadog_site": "evil.example",
            }
        )


def test_every_preset_mode_is_offered_by_setup() -> None:
    offered = {mode.value for mode in MCP_SERVERS_SETUP.modes}

    assert {"grafana", "datadog", "pagerduty", "aws"} <= offered


def test_stdio_env_values_are_kept_as_written() -> None:
    from integrations.mcp_servers import McpServerConfig

    config = McpServerConfig(
        mode="stdio", command="srv", env={"HTTP_PROXY": "", "GREETING": "  hi  "}
    )

    assert config.env == {"HTTP_PROXY": "", "GREETING": "  hi  "}


def test_answers_for_two_presets_are_refused_by_the_shared_verifier() -> None:
    # A hand-edited store or a direct apply_setup caller can bypass the picker.
    answers = {"name": "obs", "grafana_url": "http://g", "datadog_api_key": "k"}

    with pytest.raises(ValueError, match="one MCP preset"):
        McpServerConfig.from_mapping(answers)
    outcome = apply_setup(MCP_SERVERS_SETUP, answers)

    assert not outcome.ok
    assert get_integration("mcp") is None
