from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import httpx
from click.testing import CliRunner

from app.cli.__main__ import cli
from app.remote.stream import StreamEvent


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
