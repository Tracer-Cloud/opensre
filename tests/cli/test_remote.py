from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import httpx
from click.testing import CliRunner

from app.cli.__main__ import cli
from app.remote.ops import RemoteOpsError, RestartResult, ServiceStatus
from app.remote.stream import StreamEvent


class _AnsweredPrompt:
    def __init__(self, value: object) -> None:
        self._value = value

    def ask(self) -> object:
        return self._value


def _probe_health_report(*, latency_ms: int = 17) -> dict[str, object]:
    return {
        "status": "passed",
        "base_url": "http://10.0.0.1:2024",
        "latency_ms": latency_ms,
        "local_version": "2026.4.5",
        "remote_version": "2026.4.5",
        "checks": [],
        "hints": [],
        "ok": True,
    }


def test_remote_health_requires_saved_or_explicit_url() -> None:
    runner = CliRunner()

    with patch("app.cli.wizard.store.load_remote_url", return_value=None):
        result = runner.invoke(cli, ["remote", "health"])

    assert result.exit_code != 0
    assert "No remote URL configured." in result.output


def test_remote_health_uses_saved_url_and_persists_normalized_url() -> None:
    runner = CliRunner()
    client = MagicMock()
    client.base_url = "http://10.0.0.1:2024"
    client.probe_health.return_value = _probe_health_report()

    with (
        patch.dict(os.environ, {}, clear=True),
        patch("app.cli.wizard.store.load_remote_url", return_value="10.0.0.1"),
        patch("app.remote.client.RemoteAgentClient", return_value=client) as mock_client_cls,
        patch("app.cli.wizard.store.save_remote_url") as mock_save_remote_url,
    ):
        result = runner.invoke(cli, ["remote", "health"])

    assert result.exit_code == 0
    mock_client_cls.assert_called_once_with("10.0.0.1", api_key=None)
    mock_save_remote_url.assert_called_once_with("http://10.0.0.1:2024")


def test_remote_health_renders_probe_health_output() -> None:
    runner = CliRunner()
    client = MagicMock()
    client.base_url = "http://10.0.0.1:2024"
    client.probe_health.return_value = _probe_health_report(latency_ms=12)

    with (
        patch("app.cli.wizard.store.load_remote_url", return_value="10.0.0.1"),
        patch("app.remote.client.RemoteAgentClient", return_value=client),
        patch("app.cli.wizard.store.save_remote_url"),
    ):
        result = runner.invoke(cli, ["remote", "health"])

    assert result.exit_code == 0
    assert "Remote URL" in result.output
    assert "http://10.0.0.1:2024" in result.output
    assert "Latency" in result.output
    assert "12ms" in result.output


def test_remote_trigger_persists_url_after_successful_run() -> None:
    runner = CliRunner()
    client = MagicMock()
    client.base_url = "http://10.0.0.1:2024"
    client.trigger_investigation.return_value = iter([StreamEvent("end", data={})])
    renderer = MagicMock()

    with (
        patch("app.cli.wizard.store.load_remote_url", return_value="10.0.0.1"),
        patch("app.remote.client.RemoteAgentClient", return_value=client),
        patch("app.remote.renderer.StreamRenderer", return_value=renderer),
        patch("app.cli.wizard.store.save_remote_url") as mock_save_remote_url,
    ):
        result = runner.invoke(cli, ["remote", "trigger"])

    assert result.exit_code == 0
    mock_save_remote_url.assert_called_once_with("http://10.0.0.1:2024")
    renderer.render_stream.assert_called_once()


def test_remote_health_reports_timeout_cleanly() -> None:
    runner = CliRunner()
    client = MagicMock()
    client.base_url = "http://10.0.0.1:2024"
    client.probe_health.side_effect = httpx.TimeoutException("timed out")

    with patch("app.remote.client.RemoteAgentClient", return_value=client):
        result = runner.invoke(cli, ["remote", "--url", "10.0.0.1", "health"])

    assert result.exit_code == 1
    assert "Connection timed out reaching http://10.0.0.1:2024." in result.output
    assert "Instance may still be starting" in result.output


def test_remote_health_json_output() -> None:
    runner = CliRunner()
    client = MagicMock()
    client.base_url = "http://10.0.0.1:2024"
    client.probe_health.return_value = _probe_health_report(latency_ms=42)

    with (
        patch("app.remote.client.RemoteAgentClient", return_value=client),
        patch("app.cli.wizard.store.save_remote_url"),
    ):
        result = runner.invoke(cli, ["remote", "--url", "10.0.0.1", "health", "--json"])

    assert result.exit_code == 0
    assert '"status": "passed"' in result.output
    assert '"latency_ms": 42' in result.output


def test_remote_health_connect_error_is_actionable() -> None:
    runner = CliRunner()
    client = MagicMock()
    client.base_url = "http://10.0.0.1:2024"
    client.probe_health.side_effect = httpx.ConnectError("refused")

    with patch("app.remote.client.RemoteAgentClient", return_value=client):
        result = runner.invoke(cli, ["remote", "--url", "10.0.0.1", "health"])

    assert result.exit_code == 1
    assert "Could not connect to http://10.0.0.1:2024." in result.output
    assert "systemctl status opensre" in result.output


def test_remote_group_passes_api_key_to_client() -> None:
    runner = CliRunner()
    client = MagicMock()
    client.base_url = "http://10.0.0.1:2024"
    client.probe_health.return_value = _probe_health_report(latency_ms=11)

    with (
        patch("app.remote.client.RemoteAgentClient", return_value=client) as mock_client_cls,
        patch("app.cli.wizard.store.save_remote_url"),
    ):
        result = runner.invoke(
            cli,
            ["remote", "--url", "10.0.0.1", "--api-key", "secret", "health"],
        )

    assert result.exit_code == 0
    mock_client_cls.assert_called_once_with("10.0.0.1", api_key="secret")


def test_remote_ops_status_uses_provider_and_persists_scope() -> None:
    runner = CliRunner()
    provider = MagicMock()
    provider.status.return_value = ServiceStatus(
        provider="railway",
        project="proj-1",
        service="svc-1",
        deployment_id="dep-1",
        deployment_status="success",
        environment="production",
        url="https://svc-1.up.railway.app",
        health="healthy",
        metadata={"region": "us-west"},
    )

    with (
        patch(
            "app.cli.wizard.store.load_remote_ops_config",
            return_value={"provider": None, "project": None, "service": None},
        ),
        patch("app.remote.ops.resolve_remote_ops_provider", return_value=provider) as mock_resolver,
        patch("app.cli.wizard.store.save_remote_ops_config") as mock_save_scope,
    ):
        result = runner.invoke(
            cli,
            [
                "remote",
                "ops",
                "--provider",
                "railway",
                "--project",
                "proj-1",
                "--service",
                "svc-1",
                "status",
            ],
        )

    assert result.exit_code == 0
    mock_resolver.assert_called_once_with("railway")
    provider.status.assert_called_once()
    mock_save_scope.assert_called_once_with(provider="railway", project="proj-1", service="svc-1")
    assert "Provider: railway" in result.output
    assert "Health: healthy" in result.output


def test_remote_ops_status_prints_json() -> None:
    runner = CliRunner()
    provider = MagicMock()
    provider.status.return_value = ServiceStatus(
        provider="railway",
        project="proj-2",
        service="svc-2",
        deployment_id=None,
        deployment_status="building",
        environment=None,
        url=None,
        health="unknown",
        metadata={},
    )

    with (
        patch(
            "app.cli.wizard.store.load_remote_ops_config",
            return_value={"provider": "railway", "project": "proj-2", "service": "svc-2"},
        ),
        patch("app.remote.ops.resolve_remote_ops_provider", return_value=provider),
        patch("app.cli.wizard.store.save_remote_ops_config"),
    ):
        result = runner.invoke(cli, ["remote", "ops", "status", "--json"])

    assert result.exit_code == 0
    assert '"provider": "railway"' in result.output
    assert '"deployment_status": "building"' in result.output


def test_remote_ops_logs_forwards_follow_and_lines() -> None:
    runner = CliRunner()
    provider = MagicMock()

    with (
        patch(
            "app.cli.wizard.store.load_remote_ops_config",
            return_value={"provider": "railway", "project": "proj-3", "service": "svc-3"},
        ),
        patch("app.remote.ops.resolve_remote_ops_provider", return_value=provider),
        patch("app.cli.wizard.store.save_remote_ops_config") as mock_save_scope,
    ):
        result = runner.invoke(cli, ["remote", "ops", "logs", "--follow", "--lines", "50"])

    assert result.exit_code == 0
    provider.logs.assert_called_once()
    _, kwargs = provider.logs.call_args
    assert kwargs["follow"] is True
    assert kwargs["lines"] == 50
    mock_save_scope.assert_called_once_with(provider="railway", project="proj-3", service="svc-3")


def test_remote_ops_logs_does_not_persist_scope_on_failure() -> None:
    runner = CliRunner()
    provider = MagicMock()
    provider.logs.side_effect = RemoteOpsError("link failed")

    with (
        patch(
            "app.cli.wizard.store.load_remote_ops_config",
            return_value={"provider": "railway", "project": "bad-proj", "service": "svc-3"},
        ),
        patch("app.remote.ops.resolve_remote_ops_provider", return_value=provider),
        patch("app.cli.wizard.store.save_remote_ops_config") as mock_save_scope,
    ):
        result = runner.invoke(cli, ["remote", "ops", "logs", "--lines", "50"])

    assert result.exit_code == 1
    assert "link failed" in result.output
    mock_save_scope.assert_not_called()


def test_remote_ops_restart_cancelled_without_yes() -> None:
    runner = CliRunner()
    provider = MagicMock()

    with (
        patch(
            "app.cli.wizard.store.load_remote_ops_config",
            return_value={"provider": "railway", "project": "proj-4", "service": "svc-4"},
        ),
        patch("app.remote.ops.resolve_remote_ops_provider", return_value=provider),
    ):
        result = runner.invoke(cli, ["remote", "ops", "restart"], input="n\n")

    assert result.exit_code == 0
    assert "Cancelled." in result.output
    provider.restart.assert_not_called()


def test_remote_ops_restart_yes_requests_redeploy() -> None:
    runner = CliRunner()
    provider = MagicMock()
    provider.restart.return_value = RestartResult(
        provider="railway",
        project="proj-5",
        service="svc-5",
        requested=True,
        deployment_id="dep-55",
        message="Railway redeploy requested (queued).",
    )

    with (
        patch(
            "app.cli.wizard.store.load_remote_ops_config",
            return_value={"provider": None, "project": None, "service": None},
        ),
        patch("app.remote.ops.resolve_remote_ops_provider", return_value=provider),
        patch("app.cli.wizard.store.save_remote_ops_config") as mock_save_scope,
    ):
        result = runner.invoke(
            cli,
            [
                "remote",
                "ops",
                "--provider",
                "railway",
                "--project",
                "proj-5",
                "--service",
                "svc-5",
                "restart",
                "--yes",
            ],
        )

    assert result.exit_code == 0
    provider.restart.assert_called_once()
    mock_save_scope.assert_called_once_with(provider="railway", project="proj-5", service="svc-5")
    assert "Railway redeploy requested (queued)." in result.output
    assert "Deployment: dep-55" in result.output
