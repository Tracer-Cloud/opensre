"""MCP server setup: verified servers are saved by name and reach the agent."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from infrastructure.harness_providers.integration_resolution import resolve_integrations
from integrations.mcp_servers import McpServerConfig, clear_discovery_cache, mcp_server_tools
from integrations.mcp_servers.setup import MCP_SERVERS_SETUP
from integrations.setup_flow import apply_setup
from integrations.store import get_integration

_OPS_SERVER = str(Path(__file__).resolve().parents[2] / "fixtures" / "mcp" / "ops_server.py")


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clear_discovery_cache()
    monkeypatch.setattr("integrations.store.STORE_PATH", tmp_path / "integrations.json")
    monkeypatch.setattr(
        "integrations.setup_flow.sync_env_values", lambda _values, **_: tmp_path / ".env"
    )
    monkeypatch.setenv("OPS_SERVER_LOG", str(tmp_path / "calls.log"))


def test_verified_servers_are_saved_by_name_and_become_agent_tools() -> None:
    # Act
    full = apply_setup(
        MCP_SERVERS_SETUP,
        {"name": "ops", "command": sys.executable, "args": _OPS_SERVER, "read_only": "no"},
    )
    reads = apply_setup(
        MCP_SERVERS_SETUP,
        {"name": "ops-ro", "command": sys.executable, "args": _OPS_SERVER, "read_only": "yes"},
    )
    broken = apply_setup(MCP_SERVERS_SETUP, {"name": "broken", "command": "/no/such/server"})
    tools = {tool.name for tool in mcp_server_tools(resolve_integrations())}

    # Assert
    assert full.ok and "2 tool(s), 1 read-only" in full.detail
    assert reads.ok and "1 tool(s), 1 read-only" in reads.detail
    assert not broken.ok and "Could not list tools" in broken.detail
    record = get_integration("mcp") or {}
    assert [instance["name"] for instance in record["instances"]] == ["ops", "ops-ro"]
    assert tools == {
        "mcp__ops__query_range",
        "mcp__ops__silence_alert",
        "mcp__ops-ro__query_range",
    }


@pytest.mark.parametrize(
    ("raw", "mode"),
    [
        ({"command": "uvx"}, "stdio"),
        ({"url": "http://localhost:8000/mcp"}, "streamable-http"),
        ({"url": "http://x", "command": "uvx"}, "streamable-http"),
        ({"mode": "sse", "url": "http://x"}, "sse"),
    ],
)
def test_transport_follows_what_was_configured(raw: dict[str, str], mode: str) -> None:
    assert McpServerConfig.model_validate(raw).mode == mode


def test_auth_token_becomes_a_bearer_header_unless_one_is_set() -> None:
    token_only = McpServerConfig(url="http://x", auth_token="Bearer abc")
    explicit = McpServerConfig(
        url="http://x", auth_token="abc", headers={"authorization": "Basic z"}
    )

    assert token_only.request_headers == {"Authorization": "Bearer abc"}
    assert explicit.request_headers == {"authorization": "Basic z"}


def _passing_verify(_source: str, _config: dict[str, object]) -> dict[str, str]:
    return {"status": "passed", "detail": "ok"}


def test_rerunning_setup_keeps_a_servers_settings_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: a saved read-only server with a token and an allowlist.
    import dataclasses

    import integrations.cli as cli
    import integrations.mcp_servers.setup as mcp_setup
    from integrations.store import upsert_instance

    saved = {
        "url": "https://mcp.example/mcp",
        "auth_token": "tok-1",
        "read_only": "yes",
        "allowed_tools": "query_range,list_alerts",
    }
    # Another server is saved first, so prefill must come from the named one.
    upsert_instance("mcp", {"name": "ops", "credentials": {"command": "uvx", "read_only": "no"}})
    upsert_instance("mcp", {"name": "grafana", "credentials": dict(saved)})
    spec = dataclasses.replace(mcp_setup.MCP_SERVERS_SETUP, verify=_passing_verify)
    monkeypatch.setattr(mcp_setup, "MCP_SERVERS_SETUP", spec)
    monkeypatch.setattr(cli, "_select", lambda *_args, **_kwargs: "http")
    offered: dict[str, str] = {}

    def press_enter(label: str, default: str = "", secret: bool = False) -> str:
        # A required field re-asks while blank; fail instead of looping forever.
        assert label not in offered, f"re-asked {label!r}: no usable default"
        offered[label] = default
        return "grafana" if label.startswith("Server name") else default

    monkeypatch.setattr(cli, "_p", press_enter)

    # Act
    cli._setup_mcp_server()

    # Assert: every saved value was offered and kept.
    instances = {i["name"]: i for i in (get_integration("mcp") or {})["instances"]}
    instance = instances["grafana"]
    assert instances["ops"]["credentials"] == {"command": "uvx", "read_only": "no"}
    assert {key: instance["credentials"][key] for key in saved} == saved
    assert offered["Bearer token (optional, sent as Authorization)"] == "tok-1"


def test_server_setup_does_not_need_a_writable_env_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unwritable(*_args: object, **_kwargs: object) -> Path:
        raise PermissionError("read-only .env")

    monkeypatch.setattr("integrations.setup_flow.sync_env_values", unwritable)
    monkeypatch.setattr("integrations.setup_flow.sync_env_secret", unwritable)

    outcome = apply_setup(
        MCP_SERVERS_SETUP,
        {"name": "ops", "command": sys.executable, "args": _OPS_SERVER, "read_only": "no"},
    )

    assert outcome.ok, outcome.detail
    assert outcome.env_path is None
    assert [i["name"] for i in (get_integration("mcp") or {})["instances"]] == ["ops"]
