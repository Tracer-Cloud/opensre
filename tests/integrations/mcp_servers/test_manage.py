"""Managing saved MCP servers: verify each one, list each one, remove one."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from rich.console import Console

from integrations.mcp_servers import clear_discovery_cache, server_summaries
from integrations.store import get_integration, upsert_instance
from integrations.verify import format_verification_results, verify_integrations

_OPS_SERVER = str(Path(__file__).resolve().parents[2] / "fixtures" / "mcp" / "ops_server.py")


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clear_discovery_cache()
    monkeypatch.setattr("integrations.store.STORE_PATH", tmp_path / "integrations.json")
    monkeypatch.setattr("integrations.account_integrations.load_account_integrations", lambda: [])
    monkeypatch.setenv("OPS_SERVER_LOG", str(tmp_path / "calls.log"))


def _save(name: str, **credentials: object) -> None:
    upsert_instance("mcp", {"name": name, "credentials": credentials})


def _server_names() -> list[str]:
    return [i["name"] for i in (get_integration("mcp") or {}).get("instances", [])]


def test_verify_checks_every_saved_server_by_name() -> None:
    # Arrange: two working servers and one whose command does not exist.
    _save("ops", command=sys.executable, args=[_OPS_SERVER])
    _save("ops-ro", command=sys.executable, args=[_OPS_SERVER], read_only="yes")
    _save("broken", command="/no/such/server")

    # Act
    rows = verify_integrations("mcp")
    table = format_verification_results(rows)

    # Assert
    statuses = {row["instance"]: row["status"] for row in rows}
    assert statuses == {"ops": "passed", "ops-ro": "passed", "broken": "failed"}
    assert "1 read-only" in next(r["detail"] for r in rows if r["instance"] == "ops-ro")
    assert "mcp/broken" in table


def test_removing_one_server_keeps_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    from integrations.cli import cmd_remove

    monkeypatch.setattr("infrastructure.process.runtime_flags.is_yes", lambda: True)
    _save("ops", command="uvx")
    _save("grafana", url="https://grafana.example/mcp")

    cmd_remove("mcp", instance="ops")

    assert _server_names() == ["grafana"]


def test_removing_an_unknown_server_removes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    from integrations.cli import cmd_remove

    monkeypatch.setattr("infrastructure.process.runtime_flags.is_yes", lambda: True)
    _save("ops", command="uvx")

    with pytest.raises(SystemExit):
        cmd_remove("mcp", instance="opps")

    assert _server_names() == ["ops"]


def test_mcp_list_shows_each_server_without_its_credentials() -> None:
    from surfaces.shared.terminal.tables.tables import mcp_servers_table

    effective = {
        "mcp": {
            "source": "local store",
            "config": {},
            "instances": [
                {
                    "name": "grafana",
                    "config": {
                        "mode": "streamable-http",
                        "url": "https://user:pw@grafana.example/mcp?token=q-secret",
                        "headers": {"Authorization": "Bearer h-secret"},
                        "read_only": True,
                    },
                },
                {
                    "name": "ops",
                    "config": {
                        "mode": "stdio",
                        "command": "/usr/bin/ops-mcp",
                        "args": ["--token", "a-secret"],
                    },
                },
            ],
        }
    }
    results = [
        {"service": "mcp", "instance": "grafana", "status": "passed", "detail": "Connected"},
        {"service": "mcp", "instance": "ops", "status": "failed", "detail": "Could not list"},
        {"service": "github", "status": "passed", "detail": "MCP ok"},
    ]

    buffer = io.StringIO()
    console = Console(file=buffer, width=140, force_terminal=False)
    console.print(mcp_servers_table(results, server_summaries(effective)))
    out = buffer.getvalue()

    for expected in ("grafana", "https://grafana.example/mcp", "ops", "ops-mcp", "github"):
        assert expected in out
    for secret in ("q-secret", "h-secret", "a-secret", "user:pw"):
        assert secret not in out
    assert "failed" in out and "Could not list" in out


def test_repl_mcp_remove_removes_one_named_server(monkeypatch: pytest.MonkeyPatch) -> None:
    import infrastructure.analytics.capture as analytics
    from surfaces.interactive_shell.command_registry import integrations as m
    from surfaces.interactive_shell.command_registry.dispatch import dispatch_slash
    from surfaces.interactive_shell.session import Session

    monkeypatch.setattr(m, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(m, "repl_choose_one", lambda **_: "yes")
    monkeypatch.setattr(analytics, "capture_integration_removed", lambda *_: None)
    _save("ops", command="uvx")
    _save("grafana", url="https://grafana.example/mcp")
    buffer = io.StringIO()

    dispatch_slash("/mcp remove nope", Session(), Console(file=buffer))
    dispatch_slash("/mcp remove ops", Session(), Console(file=buffer))

    assert "no MCP server named" in buffer.getvalue()
    assert _server_names() == ["grafana"]
