from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from surfaces.cli.app import cli
from surfaces.cli.host import CLI_HOST_CONTEXT_KEY, CliHost


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_gateway_requires_subcommand(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["gateway"])

    assert result.exit_code != 0
    for subcommand in ("start", "stop", "status", "logs", "web"):
        assert subcommand in result.output


def test_gateway_web_serves_in_the_foreground(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("surfaces.gateway_entry.serve_web", lambda: calls.append("web"))

    result = runner.invoke(cli, ["gateway", "web"])

    assert result.exit_code == 0
    assert calls == ["web"]


def test_gateway_start_foreground_runs_the_host_runner(runner: CliRunner) -> None:
    calls: list[str] = []
    host = CliHost(start_gateway_foreground=lambda: calls.append("started"))

    result = runner.invoke(
        cli, ["gateway", "start", "--foreground"], obj={CLI_HOST_CONTEXT_KEY: host}
    )

    assert result.exit_code == 0
    assert calls == ["started"]
    assert "OpenSRE gateway" in result.output


def test_gateway_start_foreground_without_a_host_fails_clearly(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["gateway", "start", "--foreground"])

    assert result.exit_code != 0
    assert "opensre entrypoint" in result.output


def test_gateway_entry_wires_slash_ports() -> None:
    """Production entry must inject headless slash ports into GatewayController."""
    import gateway.core.lifecycle.controller as manager_module

    mock_manager = MagicMock()
    with patch.object(manager_module, "GatewayController", return_value=mock_manager) as ctor:
        from surfaces.gateway_entry import main as entry_main

        entry_main()

    assert ctor.call_args.kwargs["slash_ports_factory"].__name__ == "gateway_slash_ports_factory"
    mock_manager.start_gateway.assert_called_once_with(wait=True)


def test_gateway_start_spawns_daemon(runner: CliRunner) -> None:
    from surfaces.shared.gateway_entrypoint import gateway_entry_argv

    with patch(
        "surfaces.cli.commands.gateway.start_gateway_daemon",
        return_value=(True, "Telegram gateway started (pid 4242)."),
    ) as mock_start:
        result = runner.invoke(cli, ["gateway", "start"])

    assert result.exit_code == 0
    mock_start.assert_called_once_with(argv=gateway_entry_argv())
    assert "pid 4242" in result.output
    assert "Logs:" in result.output


def test_gateway_status_reports_stopped(runner: CliRunner) -> None:
    with patch("surfaces.cli.commands.gateway.gateway_daemon_pid", return_value=None):
        result = runner.invoke(cli, ["gateway", "status"])

    assert result.exit_code == 0
    assert "stopped" in result.output


def test_gateway_stop_reports_result(runner: CliRunner) -> None:
    with patch(
        "surfaces.cli.commands.gateway.stop_gateway_daemon",
        return_value=(True, "Telegram gateway stopped (pid 4242)."),
    ):
        result = runner.invoke(cli, ["gateway", "stop"])

    assert result.exit_code == 0
    assert "stopped" in result.output
