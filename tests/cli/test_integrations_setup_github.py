"""GitHub setup hands off without credentials, uploads, or premature success."""

from unittest.mock import Mock

from click.testing import CliRunner

from integrations.cli import cmd_setup
from integrations.github import cli_setup
from integrations.setup import SetupPending
from surfaces.cli.app import cli


def test_github_setup_only_opens_app(monkeypatch, capsys, local_browser):
    monkeypatch.setattr(cli_setup, "github_setup_url", lambda: "https://app.opensre.com/home")
    monkeypatch.setattr(cli_setup, "load_account_record", lambda: None)
    monkeypatch.setattr(
        "integrations.setup_flow.upsert_integration",
        Mock(side_effect=AssertionError("credential write")),
    )
    monkeypatch.setattr(
        "integrations.setup_flow.push_webapp_org_integration",
        Mock(side_effect=AssertionError("upload")),
    )
    assert cmd_setup("github") == SetupPending("github", "https://app.opensre.com/home")
    assert local_browser == ["https://app.opensre.com/home"]
    text = capsys.readouterr().out
    assert "pending" in text
    assert "opensre account login" in text
    assert "Saved" not in text
    assert "device" not in text


def test_github_setup_on_a_server_prints_the_link_without_launching_a_browser(monkeypatch, capsys):
    monkeypatch.setattr("infrastructure.terminal.browser.browser_is_remote", lambda: True)
    monkeypatch.setattr("webbrowser.open", Mock(side_effect=AssertionError("launched")))
    cli_setup.setup_github()
    assert "in a browser on any device" in capsys.readouterr().out


def test_pending_cli_handoff_does_not_emit_success(monkeypatch, local_browser):
    completed = Mock()
    verified = Mock()
    monkeypatch.setattr(cli_setup, "github_setup_url", lambda: "https://app.opensre.com/home")
    monkeypatch.setattr(
        "surfaces.cli.commands.integrations.capture_integration_setup_started", Mock()
    )
    monkeypatch.setattr(
        "surfaces.cli.commands.integrations.capture_integration_setup_completed", completed
    )
    monkeypatch.setattr("surfaces.cli.commands.integrations.capture_integration_verified", verified)
    monkeypatch.setattr(
        "surfaces.shared.integration_telemetry.capture_github_connection_snapshot", Mock()
    )

    result = CliRunner().invoke(cli, ["integrations", "setup", "github"])

    assert result.exit_code == 0, result.output
    assert "pending" in result.output
    completed.assert_not_called()
    verified.assert_not_called()
