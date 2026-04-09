from __future__ import annotations

import contextlib
import types
from unittest.mock import patch

from click.testing import CliRunner

from app.cli.__main__ import cli
from app.integrations.cli import _setup_github, _validate_github


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


def test_setup_github_validates_before_saving() -> None:
    """_setup_github must validate credentials before persisting them (issue #419)."""
    ok_result = types.SimpleNamespace(ok=True, detail="Validated for testuser; 12 tools")
    with (
        patch("app.integrations.cli._p", side_effect=["2", "https://api.githubcopilot.com/mcp/", "tok", "repos"]),
        patch("app.integrations.cli._validate_github", return_value=ok_result) as mock_val,
        patch("app.integrations.cli.upsert_integration") as mock_save,
    ):
        _setup_github()

    mock_val.assert_called_once()
    mock_save.assert_called_once()


def test_setup_github_aborts_on_validation_failure() -> None:
    """_setup_github must NOT save credentials when validation fails (issue #419)."""
    bad_result = types.SimpleNamespace(ok=False, detail="auth failed")
    with (
        patch("app.integrations.cli._p", side_effect=["2", "https://api.githubcopilot.com/mcp/", "bad-tok", "repos"]),
        patch("app.integrations.cli._validate_github", return_value=bad_result),
        patch("app.integrations.cli.upsert_integration") as mock_save,
        contextlib.suppress(SystemExit),
    ):
        _setup_github()

    mock_save.assert_not_called()


def test_validate_github_calls_mcp_validation() -> None:
    """_validate_github builds config and delegates to validate_github_mcp_config."""
    ok_result = types.SimpleNamespace(ok=True, detail="ok", tool_names=(), authenticated_user="me")
    with patch(
        "app.integrations.cli.validate_github_mcp_config",
        return_value=ok_result,
    ) as mock_val:
        result = _validate_github({
            "url": "https://example.com/mcp/",
            "mode": "streamable-http",
            "auth_token": "ghp_test",
            "toolsets": ["repos"],
        })

    assert result.ok is True
    mock_val.assert_called_once()


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
