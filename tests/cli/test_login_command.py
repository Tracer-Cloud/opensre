"""Canonical connection commands preserve billing, identity, and legacy scripts."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from config.account import AccountRecord, account_llm_route, save_account_record
from config.llm_auth.credentials import status
from surfaces.cli.app import cli


@pytest.mark.parametrize("local_account", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("choose_local", [False, True])
def test_provider_connection_lifecycle_preserves_hosted_inference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_account: bool,
    legacy: bool,
    choose_local: bool,
) -> None:
    from surfaces.shared.llm_setup import model_discovery

    env_path = tmp_path / ".env"
    account_path = tmp_path / "account.json"
    monkeypatch.setenv("OPENSRE_PROJECT_ENV_PATH", str(env_path))
    monkeypatch.setenv("OPENSRE_ACCOUNT_METADATA_PATH", str(account_path))
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "auth.json"))
    monkeypatch.setenv("OPENSRE_ACCOUNT_TOKEN", "synthetic-account-token")
    monkeypatch.setenv("OPENSRE_WEBAPP_URL", "https://app.example")
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_CONNECTION", "account")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", env_path)
    monkeypatch.setattr("surfaces.shared.llm_setup.env_sync.PROJECT_ENV_PATH", env_path)
    monkeypatch.setattr("config.setup_store.get_store_path", lambda: tmp_path / "setup.json")
    monkeypatch.setattr(
        model_discovery, "discover_models", lambda _: [("gpt-5.4-mini", "Test model")]
    )
    if local_account:
        save_account_record(
            AccountRecord(
                user_id="test-user",
                organization_id="test-org",
                email="test@example.com",
                app_url="https://app.example",
                signed_in_at="2026-10-30T00:00:00Z",
                token_expires_at="2026-11-01T00:00:00Z",
            )
        )
    account_before = account_path.read_bytes() if local_account else None
    hosted_before = account_llm_route()
    assert hosted_before is not None
    runner = CliRunner()
    connect = ["auth", "login"] if legacy else ["login"]
    result = runner.invoke(
        cli,
        [
            *connect,
            "openai",
            "--api-key",
            "synthetic-api-key",
            "--no-validate",
            "--no-open-browser",
        ],
    )
    assert result.exit_code == 0, result.output
    assert status("openai").configured
    assert account_llm_route() == hosted_before
    if choose_local:
        selected = runner.invoke(cli, ["model", "set", "openai", "gpt-5.4-mini"])
        assert selected.exit_code == 0, selected.output
        assert account_llm_route() is None
    inspected = runner.invoke(cli, ["login", "status", "openai"])
    assert inspected.exit_code == 0, inspected.output
    assert "synthetic-api-key" not in inspected.output
    disconnect = ["auth", "logout"] if legacy else ["login", "disconnect"]
    result = runner.invoke(cli, [*disconnect, "openai"])
    assert result.exit_code == 0, result.output
    assert not status("openai").configured
    assert "OPENAI_API_KEY" not in os.environ
    assert account_llm_route() == (None if choose_local else hosted_before)
    assert os.environ["OPENSRE_LLM_CONNECTION"] == ("local" if choose_local else "account")
    assert os.environ["OPENSRE_ACCOUNT_TOKEN"] == "synthetic-account-token"
    assert (account_path.read_bytes() if account_path.exists() else None) == account_before


@pytest.mark.parametrize("args", [[], ["connect"], ["verify"], ["disconnect"]])
def test_incomplete_noninteractive_commands_do_not_open_menus_or_write(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    from surfaces.cli.commands import login

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Noninteractive command opened a picker")

    monkeypatch.setattr(login, "repl_tty_interactive", lambda: False)
    monkeypatch.setattr(login, "prompt_login_action", forbidden)
    monkeypatch.setattr(login, "prompt_auth_provider", forbidden)
    monkeypatch.setattr(login, "prompt_connection_target", forbidden)
    result = CliRunner().invoke(cli, ["login", *args])
    assert result.exit_code == 2, result.output
    assert "Supply" in result.output
