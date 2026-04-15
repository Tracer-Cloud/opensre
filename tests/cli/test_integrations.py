from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from app.cli.__main__ import cli


def test_integrations_show_redacts_api_token() -> None:
    runner = CliRunner()

    with patch(
        "app.integrations.cli.get_integration",
        return_value={
            "id": "vercel-1234",
            "service": "vercel",
            "status": "active",
            "credentials": {
                "api_token": "vcp_sensitive_token_value",
                "team_id": "team_123",
            },
        },
    ):
        result = runner.invoke(cli, ["integrations", "show", "vercel"])

    assert result.exit_code == 0
    assert "vcp_****" in result.output
    assert "vcp_sensitive_token_value" not in result.output


def test_integrations_setup_accepts_github() -> None:
    runner = CliRunner()

    with (
        patch("app.cli.commands.integrations.capture_integration_setup_started"),
        patch("app.cli.commands.integrations.capture_integration_setup_completed"),
        patch("app.cli.commands.integrations.capture_integration_verified"),
        patch("app.integrations.cli.cmd_setup") as mock_setup,
        patch("app.integrations.cli.cmd_verify", return_value=0) as mock_verify,
    ):
        mock_setup.return_value = "github"
        result = runner.invoke(cli, ["integrations", "setup", "github"])

    assert result.exit_code == 0
    mock_setup.assert_called_once_with("github")
    mock_verify.assert_called_once_with("github")


def test_integrations_setup_accepts_vercel() -> None:
    runner = CliRunner()

    with (
        patch("app.cli.commands.integrations.capture_integration_setup_started"),
        patch("app.cli.commands.integrations.capture_integration_setup_completed"),
        patch("app.cli.commands.integrations.capture_integration_verified"),
        patch("app.integrations.cli.cmd_setup") as mock_setup,
        patch("app.integrations.cli.cmd_verify", return_value=1) as mock_verify,
    ):
        mock_setup.return_value = "vercel"
        result = runner.invoke(cli, ["integrations", "setup", "vercel"])

    assert result.exit_code == 1
    mock_setup.assert_called_once_with("vercel")
    mock_verify.assert_called_once_with("vercel")


def test_integrations_setup_skips_auto_verify_for_unverifiable_service() -> None:
    runner = CliRunner()

    with (
        patch("app.cli.commands.integrations.capture_integration_setup_started"),
        patch("app.cli.commands.integrations.capture_integration_setup_completed"),
        patch("app.cli.commands.integrations.capture_integration_verified"),
        patch("app.integrations.cli.cmd_setup") as mock_setup,
        patch("app.integrations.cli.cmd_verify") as mock_verify,
    ):
        mock_setup.return_value = "opensearch"
        result = runner.invoke(cli, ["integrations", "setup", "opensearch"])

    assert result.exit_code == 0
    mock_setup.assert_called_once_with("opensearch")
    mock_verify.assert_not_called()


def test_integrations_verify_accepts_github() -> None:
    runner = CliRunner()

    with (
        patch("app.cli.commands.integrations.capture_integration_verified") as mock_capture,
        patch("app.integrations.cli.cmd_verify", return_value=0) as mock_verify,
    ):
        result = runner.invoke(cli, ["integrations", "verify", "github"])

    assert result.exit_code == 0
    mock_verify.assert_called_once_with(
        "github",
        send_slack_test=False,
    )
    mock_capture.assert_called_once_with("github")


def test_integrations_verify_accepts_openclaw() -> None:
    runner = CliRunner()

    with (
        patch("app.cli.commands.integrations.capture_integration_verified") as mock_capture,
        patch("app.integrations.cli.cmd_verify", return_value=1) as mock_verify,
    ):
        result = runner.invoke(cli, ["integrations", "verify", "openclaw"])

    assert result.exit_code == 1
    mock_verify.assert_called_once_with(
        "openclaw",
        send_slack_test=False,
    )
    mock_capture.assert_called_once_with("openclaw")
