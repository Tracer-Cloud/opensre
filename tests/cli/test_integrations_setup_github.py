"""GitHub setup hands off without credentials, uploads, or premature success."""

from unittest.mock import Mock

from click.testing import CliRunner

from integrations.cli import cmd_setup
from integrations.github import cli_setup
from integrations.setup import SetupPending
from surfaces.cli.app import cli


def test_github_setup_only_opens_app(monkeypatch, capsys):
    opened = Mock(return_value=True)
    monkeypatch.setattr(cli_setup.webbrowser, "open", opened)
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
    opened.assert_called_once_with("https://app.opensre.com/home")
    text = capsys.readouterr().out
    assert "pending" in text
    assert "opensre account login" in text
    assert "Saved" not in text
    assert "device" not in text


def test_github_setup_prints_url_when_browser_unavailable(monkeypatch, capsys):
    monkeypatch.setattr(cli_setup.webbrowser, "open", lambda _url: False)
    cli_setup.setup_github()
    assert "Open https://" in capsys.readouterr().out


def test_pending_cli_handoff_does_not_emit_success(monkeypatch):
    completed = Mock()
    verified = Mock()
    monkeypatch.setattr(cli_setup.webbrowser, "open", lambda _url: True)
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
