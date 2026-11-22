"""MCP server setup: verified servers are saved by name and reach the agent."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from infrastructure.harness_providers.integration_resolution import resolve_integrations
from integrations.mcp_servers import McpServerConfig, clear_discovery_cache, mcp_server_tools
from integrations.mcp_servers.setup import MCP_SERVERS_SETUP
from integrations.setup_flow import IntegrationSetupSpec, apply_setup
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


def test_setup_into_an_inactive_record_makes_the_server_available(tmp_path: Path) -> None:
    # Arrange: a disabled mcp record already exists in the store.
    import json

    store = tmp_path / "integrations.json"
    store.write_text(
        json.dumps(
            {
                "version": 2,
                "integrations": [
                    {"id": "mcp-old", "service": "mcp", "status": "inactive", "instances": []}
                ],
            }
        )
    )

    # Act
    outcome = apply_setup(
        MCP_SERVERS_SETUP, {"name": "ops", "command": sys.executable, "args": _OPS_SERVER}
    )
    tools = {tool.name for tool in mcp_server_tools(resolve_integrations())}

    # Assert
    assert outcome.ok, outcome.detail
    assert tools == {"mcp__ops__query_range", "mcp__ops__silence_alert"}


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


def test_rerunning_setup_keeps_a_stored_argument_list_intact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange: a hand-configured stdio server whose args list has a path with spaces.
    import shutil

    import integrations.cli as cli
    from integrations.store import upsert_instance

    script = tmp_path / "My Servers" / "ops server.py"
    script.parent.mkdir()
    shutil.copy(_OPS_SERVER, script)
    stored_args = [str(script)]
    upsert_instance(
        "mcp",
        {"name": "ops", "credentials": {"command": sys.executable, "args": stored_args}},
    )
    monkeypatch.setattr(cli, "_select", lambda *_args, **_kwargs: "stdio")
    monkeypatch.setattr(
        cli,
        "_p",
        lambda label, default="", **_kwargs: "ops" if label.startswith("Server name") else default,
    )

    # Act: press Enter at every prompt; the real verifier starts the server.
    cli._setup_mcp_server()

    # Assert
    [instance] = (get_integration("mcp") or {})["instances"]
    config = McpServerConfig.model_validate(instance["credentials"])
    assert config.args == tuple(stored_args)


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


def test_a_new_server_never_inherits_another_servers_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dataclasses

    import integrations.cli as cli
    import integrations.mcp_servers.setup as mcp_setup
    from integrations.store import upsert_instance

    upsert_instance(
        "mcp",
        {"name": "grafana", "credentials": {"url": "https://a/mcp", "auth_token": "secret-a"}},
    )
    sent: list[dict[str, object]] = []

    def record_verify(_source: str, config: dict[str, object]) -> dict[str, str]:
        sent.append(dict(config))
        return {"status": "passed", "detail": "ok"}

    spec = dataclasses.replace(mcp_setup.MCP_SERVERS_SETUP, verify=record_verify)
    monkeypatch.setattr(mcp_setup, "MCP_SERVERS_SETUP", spec)
    monkeypatch.setattr(cli, "_select", lambda *_args, **_kwargs: "http")
    answers = {"Server name": "other", "Server URL": "https://b/mcp"}

    def answer(label: str, default: str = "", secret: bool = False) -> str:
        return next((v for k, v in answers.items() if label.startswith(k)), default)

    monkeypatch.setattr(cli, "_p", answer)

    cli._setup_mcp_server()

    assert "auth_token" not in sent[0]
    instances = {i["name"]: i for i in (get_integration("mcp") or {})["instances"]}
    assert not instances["other"]["credentials"].get("auth_token")


def test_quoted_arguments_with_spaces_reach_the_server(tmp_path: Path) -> None:
    spaced = tmp_path / "My Servers" / "ops server.py"
    spaced.parent.mkdir()
    spaced.write_text(Path(_OPS_SERVER).read_text())

    outcome = apply_setup(
        MCP_SERVERS_SETUP,
        {"name": "ops", "command": sys.executable, "args": f'"{spaced}"', "read_only": "no"},
    )

    assert outcome.ok, outcome.detail
    assert "2 tool(s)" in outcome.detail


def _capturing_spec(seen: list[dict[str, object]]) -> IntegrationSetupSpec:
    import dataclasses

    def verify(_source: str, config: dict[str, object]) -> dict[str, str]:
        seen.append(dict(config))
        return {"status": "passed", "detail": "ok"}

    return dataclasses.replace(MCP_SERVERS_SETUP, verify=verify)


def test_updating_a_server_keeps_settings_the_form_does_not_edit() -> None:
    # Arrange: a hand-tuned SSE server with a custom header and a long timeout.
    from integrations.store import upsert_instance

    upsert_instance(
        "mcp",
        {
            "name": "grafana",
            "credentials": {
                "url": "https://old.example/sse",
                "mode": "sse",
                "headers": {"X-Api-Key": "k-1", "Authorization": "Bearer old"},
                "timeout_seconds": 90,
            },
        },
    )
    seen: list[dict[str, object]] = []

    # Act: re-run setup with a new URL and a new token.
    outcome = apply_setup(
        _capturing_spec(seen),
        {"name": "grafana", "url": "https://new.example/sse", "auth_token": "new"},
    )

    # Assert: the check and the saved server both keep the untouched settings.
    assert outcome.ok, outcome.detail
    [instance] = (get_integration("mcp") or {})["instances"]
    for config in (seen[0], instance["credentials"]):
        assert config["headers"] == {"X-Api-Key": "k-1"}
        assert config["timeout_seconds"] == 90
        assert config["mode"] == "sse"
    server = McpServerConfig.model_validate(
        {k: v for k, v in instance["credentials"].items() if k in McpServerConfig.model_fields}
    )
    assert server.request_headers["Authorization"] == "Bearer new"


def test_switching_a_server_to_a_local_command_drops_http_only_settings() -> None:
    from integrations.store import upsert_instance

    upsert_instance(
        "mcp",
        {
            "name": "ops",
            "credentials": {
                "url": "https://old.example/sse",
                "mode": "sse",
                "headers": {"X-Api-Key": "k-1"},
                "timeout_seconds": 90,
            },
        },
    )
    seen: list[dict[str, object]] = []

    outcome = apply_setup(
        _capturing_spec(seen),
        {"name": "ops", "command": sys.executable, "args": _OPS_SERVER},
    )

    assert outcome.ok, outcome.detail
    [instance] = (get_integration("mcp") or {})["instances"]
    assert "headers" not in instance["credentials"]
    assert "mode" not in instance["credentials"]
    assert instance["credentials"]["timeout_seconds"] == 90
