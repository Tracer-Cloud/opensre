"""GitHub setup tests covering GitHub CLI detection, session reuse, and MCP app handoff."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from integrations.github import cli_setup
from integrations.github.cli_probe import (
    GitHubCLIAuthStatus,
    GitHubCLIHostAccount,
    GitHubCLIProbeResult,
)
from integrations.setup import SetupPending
from integrations.setup_flow import SetupUI
from surfaces.cli.app import cli


class DummySetupUI(SetupUI):
    """Test fake for SetupUI providing pre-scripted prompt responses."""

    def __init__(self, choices: list[str]) -> None:
        self.choices = iter(choices)
        self.messages: list[str] = []

    def say(self, message: str) -> None:
        self.messages.append(message)

    def step(self, number: str, title: str) -> None:
        self.messages.append(f"Step {number}: {title}")

    def choose(self, _message: str, _choices: Sequence[tuple[str, str]]) -> str:
        try:
            return next(self.choices)
        except StopIteration:
            raise EOFError("No more scripted choices") from None

    def value(self, _message: str, *, default: str = "", secret: bool = False) -> str:
        del secret
        return default


def _fake_probe_missing() -> GitHubCLIProbeResult:
    return GitHubCLIProbeResult(installed=False, detail="gh not found")


def _fake_probe_authenticated(
    user: str = "testuser", host: str = "github.com"
) -> GitHubCLIProbeResult:
    acc = GitHubCLIHostAccount(hostname=host, username=user, active=True)
    return GitHubCLIProbeResult(
        installed=True,
        binary_path="/usr/bin/gh",
        version="2.45.0",
        auth_status=GitHubCLIAuthStatus.AUTHENTICATED,
        active_account=acc,
        accounts=(acc,),
        detail=f"Authenticated as @{user} on {host}.",
    )


def _fake_probe_unauthenticated() -> GitHubCLIProbeResult:
    return GitHubCLIProbeResult(
        installed=True,
        binary_path="/usr/bin/gh",
        version="2.45.0",
        auth_status=GitHubCLIAuthStatus.NOT_AUTHENTICATED,
        detail="Not logged in",
    )


def _fake_probe_unknown() -> GitHubCLIProbeResult:
    return GitHubCLIProbeResult(
        installed=True,
        binary_path="/usr/bin/gh",
        version="2.45.0",
        auth_status=GitHubCLIAuthStatus.UNKNOWN,
        detail="Command timed out",
    )


def test_github_setup_only_opens_app_when_chosen(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    opened = Mock(return_value=True)
    monkeypatch.setattr(cli_setup.webbrowser, "open", opened)
    monkeypatch.setattr(cli_setup, "github_setup_url", lambda: "https://app.opensre.com/home")
    monkeypatch.setattr(cli_setup, "load_account_record", lambda: None)
    monkeypatch.setattr(cli_setup, "detect_github_cli", _fake_probe_missing)

    ui = DummySetupUI(["app"])
    assert cli_setup.setup_github(ui=ui) == SetupPending("github", "https://app.opensre.com/home")
    opened.assert_called_once_with("https://app.opensre.com/home")
    text = capsys.readouterr().out
    assert "pending" in text
    assert "opensre account login" in text


def test_github_setup_prints_url_when_browser_unavailable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli_setup.webbrowser, "open", lambda _url: False)
    monkeypatch.setattr(cli_setup, "github_setup_url", lambda: "https://app.opensre.com/home")
    monkeypatch.setattr(cli_setup, "detect_github_cli", _fake_probe_missing)

    ui = DummySetupUI(["app"])
    cli_setup.setup_github(ui=ui)
    assert "Open https://" in capsys.readouterr().out


def test_pending_cli_handoff_does_not_emit_success(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = Mock()
    verified = Mock()
    monkeypatch.setattr(cli_setup.webbrowser, "open", lambda _url: True)
    monkeypatch.setattr(cli_setup, "github_setup_url", lambda: "https://app.opensre.com/home")
    monkeypatch.setattr(cli_setup, "detect_github_cli", _fake_probe_missing)
    monkeypatch.setattr(cli_setup, "TerminalSetupUI", lambda: DummySetupUI(["app"]))
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


def test_github_setup_user_selects_existing_cli_session(monkeypatch: pytest.MonkeyPatch) -> None:
    saved_entries: list[tuple[str, dict[str, Any]]] = []

    def fake_upsert(service: str, entry: dict[str, Any]) -> None:
        saved_entries.append((service, entry))

    monkeypatch.setattr(cli_setup, "upsert_integration", fake_upsert)
    monkeypatch.setattr(
        cli_setup, "detect_github_cli", lambda: _fake_probe_authenticated("octocat", "github.com")
    )

    ui = DummySetupUI(["cli"])
    outcome = cli_setup.setup_github(ui=ui)

    assert outcome == "github_cli"
    assert len(saved_entries) == 1
    svc, entry = saved_entries[0]
    assert svc == "github_cli"
    assert entry["credentials"] == {
        "auth_mode": "github_cli",
        "hostname": "github.com",
        "username": "octocat",
    }
    # Security assertion: ensure NO tokens or secrets are stored
    assert "token" not in entry["credentials"]
    assert "auth_token" not in entry["credentials"]
    assert any(
        "Configured GitHub integration to use local GitHub CLI session" in m for m in ui.messages
    )


def test_github_setup_preserves_existing_mcp_github_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    from integrations.store import get_integration, replace_integrations

    monkeypatch.setattr("integrations.store.STORE_PATH", tmp_path / "integrations.json")
    # Pre-populate with existing MCP GitHub record
    replace_integrations(
        [
            {
                "service": "github",
                "status": "active",
                "credentials": {
                    "auth_token": "ghp_existing_token_12345",
                },
            }
        ]
    )
    monkeypatch.setattr(
        cli_setup, "detect_github_cli", lambda: _fake_probe_authenticated("octocat", "github.com")
    )
    ui = DummySetupUI(["cli"])
    outcome = cli_setup.setup_github(ui=ui)

    assert outcome == "github_cli"
    github_record = get_integration("github")
    cli_record = get_integration("github_cli")

    assert github_record is not None
    assert cli_record is not None
    assert github_record["credentials"]["auth_token"] == "ghp_existing_token_12345"
    assert cli_record["credentials"]["auth_mode"] == "github_cli"
    assert cli_record["credentials"]["username"] == "octocat"


def test_github_setup_user_with_cli_chooses_app_mcp_instead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_setup.webbrowser, "open", lambda _url: True)
    monkeypatch.setattr(cli_setup, "github_setup_url", lambda: "https://app.opensre.com/home")
    monkeypatch.setattr(
        cli_setup, "detect_github_cli", lambda: _fake_probe_authenticated("octocat", "github.com")
    )

    ui = DummySetupUI(["app"])
    outcome = cli_setup.setup_github(ui=ui)

    assert outcome == SetupPending("github", "https://app.opensre.com/home")


def test_github_setup_unauthenticated_cli_retry_then_login(monkeypatch: pytest.MonkeyPatch) -> None:
    saved_entries: list[tuple[str, dict[str, Any]]] = []
    call_count = 0

    def probe_sequence() -> GitHubCLIProbeResult:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _fake_probe_unauthenticated()
        return _fake_probe_authenticated("octocat", "github.com")

    monkeypatch.setattr(cli_setup, "detect_github_cli", probe_sequence)
    monkeypatch.setattr(cli_setup, "upsert_integration", lambda s, e: saved_entries.append((s, e)))

    # First prompt: "retry" after running `gh auth login`, second prompt: "cli"
    ui = DummySetupUI(["retry", "cli"])
    outcome = cli_setup.setup_github(ui=ui)

    assert outcome == "github_cli"
    assert call_count == 2
    assert len(saved_entries) == 1
    assert saved_entries[0][0] == "github_cli"


def test_github_setup_missing_cli_retry_then_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_setup.webbrowser, "open", lambda _url: True)
    monkeypatch.setattr(cli_setup, "github_setup_url", lambda: "https://app.opensre.com/home")

    call_count = 0

    def probe_sequence() -> GitHubCLIProbeResult:
        nonlocal call_count
        call_count += 1
        return _fake_probe_missing()

    monkeypatch.setattr(cli_setup, "detect_github_cli", probe_sequence)

    # First retry, then select app
    ui = DummySetupUI(["retry", "app"])
    outcome = cli_setup.setup_github(ui=ui)

    assert isinstance(outcome, SetupPending)
    assert call_count == 2


def test_github_setup_unknown_probe_status_retry_then_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_setup.webbrowser, "open", lambda _url: True)
    monkeypatch.setattr(cli_setup, "github_setup_url", lambda: "https://app.opensre.com/home")
    monkeypatch.setattr(cli_setup, "detect_github_cli", _fake_probe_unknown)

    ui = DummySetupUI(["retry", "app"])
    outcome = cli_setup.setup_github(ui=ui)

    assert isinstance(outcome, SetupPending)


def test_github_setup_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_setup, "detect_github_cli", _fake_probe_missing)

    ui = DummySetupUI(["cancel"])
    with pytest.raises(SystemExit) as exc:
        cli_setup.setup_github(ui=ui)
    assert exc.value.code == 1


def test_mcp_authentication_status_unchanged_by_local_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifying local store record with auth_mode='github_cli' does not pass MCP validation."""
    from integrations.github.verifier import verify_github

    local_cli_config = {
        "auth_mode": "github_cli",
        "hostname": "github.com",
        "username": "octocat",
    }
    # Local CLI credentials have no auth_token / mcp url
    result = verify_github("local store", local_cli_config)
    assert result["status"] == "missing"


def test_remote_execution_does_not_inherit_local_cli_ambient_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
) -> None:
    """Local CLI onboarding persists metadata only; remote/headless processes cannot read a token."""
    from integrations.catalog import classify_integrations
    from integrations.github.rest_token import github_rest_token
    from integrations.store import load_integrations, replace_integrations

    monkeypatch.setattr("integrations.store.STORE_PATH", tmp_path / "integrations.json")
    replace_integrations(
        [
            {
                "service": "github",
                "status": "active",
                "credentials": {
                    "auth_mode": "github_cli",
                    "hostname": "github.com",
                    "username": "octocat",
                },
            }
        ]
    )
    local = load_integrations()
    # Ensure no token is resolved for remote/agent REST execution from the local CLI metadata
    assert not github_rest_token(classify_integrations(local))
