from __future__ import annotations

from pathlib import Path
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


def test_gateway_logs_rejects_negative_lines(runner: CliRunner) -> None:
    for invalid in ("-1", "-5", "-10"):
        result = runner.invoke(cli, ["gateway", "logs", "-n", invalid])
        assert result.exit_code == 2
        assert "Invalid value for '-n' / '--lines'" in result.output
        assert "is not in the range x>=0" in result.output


def test_gateway_logs_accepts_non_negative_lines(runner: CliRunner, tmp_path: Path) -> None:
    from gateway.core.process.supervision import read_gateway_log_tail

    log_file = tmp_path / "gateway.log"
    log_file.write_text("line 1\nline 2\nline 3\n")

    with (
        patch("gateway.core.process.supervision.GATEWAY_LOG_FILE", log_file),
        patch("surfaces.cli.commands.gateway.GATEWAY_LOG_FILE", log_file),
    ):
        # Defensive function guard tests
        assert read_gateway_log_tail(-5) == ""
        assert read_gateway_log_tail(0) == ""
        assert read_gateway_log_tail(2) == "line 2\nline 3"

        # CLI invocation with positive lines
        result_pos = runner.invoke(cli, ["gateway", "logs", "-n", "2"])
        assert result_pos.exit_code == 0
        assert "line 2\nline 3" in result_pos.output
        assert "line 1" not in result_pos.output

        # CLI invocation with zero lines
        result_zero = runner.invoke(cli, ["gateway", "logs", "-n", "0"])
        assert result_zero.exit_code == 0
