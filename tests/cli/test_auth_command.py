from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from config.llm_credentials import resolve_env_credential
from surfaces.cli.app import cli


@pytest.mark.parametrize(
    "method,provider,excluded",
    [
        ("Subscription", "chatgpt", {"openai", "anthropic", "claude-code"}),
        ("API key", "openai", {"chatgpt", "claude-code"}),
        ("CLI-managed", "claude-code", {"anthropic", "chatgpt"}),
        ("Local / cloud", "ollama", {"openai", "chatgpt", "claude-code"}),
    ],
)
def test_login_groups_sources_in_subcommand_submenus(monkeypatch, method, provider, excluded):
    from surfaces.shared.llm_setup import auth_menu as auth
    from surfaces.shared.llm_setup.auth_profiles import resolve_auth_profile

    seen = []
    profile = resolve_auth_profile(provider)
    answers = iter([method, profile.label])
    monkeypatch.setattr(auth, "configured_profile_name", lambda: provider)

    def choose(**kwargs):
        seen.append(kwargs)
        return next(answers)

    monkeypatch.setattr(auth, "repl_choose_subcommand", choose)
    assert auth.prompt_auth_provider().name == provider
    assert len(seen) == 2
    assert {name for name, _ in seen[0]["options"]} == {
        "Subscription",
        "API key",
        "CLI-managed",
        "Local / cloud",
    }
    provider_labels = {name for name, _ in seen[1]["options"]}
    assert profile.label in provider_labels
    assert not provider_labels.intersection(resolve_auth_profile(name).label for name in excluded)
    assert seen[0]["parent"] == "/login"
    assert seen[1]["parent"] == f"/login {method}"
    assert seen[0]["initial_value"] == method
    assert seen[1]["initial_value"] == profile.label


def test_login_provider_escape_returns_to_method_submenu(monkeypatch):
    from surfaces.shared.llm_setup import auth_menu as auth

    answers = iter(["Subscription", None, "API key", "OpenAI API key"])
    parents = []
    monkeypatch.setattr(auth, "configured_profile_name", lambda: "chatgpt")

    def choose(**kwargs):
        parents.append(kwargs["parent"])
        return next(answers)

    monkeypatch.setattr(auth, "repl_choose_subcommand", choose)
    assert auth.prompt_auth_provider().name == "openai"
    assert parents == ["/login", "/login Subscription", "/login", "/login API key"]


def test_subscription_model_selector_can_cancel(monkeypatch) -> None:
    import click

    from surfaces.shared.llm_setup import authorization as subscription_auth

    monkeypatch.setattr(subscription_auth.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        subscription_auth, "repl_choose_subcommand", lambda **_kwargs: None, raising=False
    )
    with pytest.raises(click.Abort):
        subscription_auth.choose_subscription_model(
            [("gpt-test", "Test model")], provider="chatgpt"
        )


def _patch_auth_env(monkeypatch, tmp_path: Path) -> Path:
    env_path = tmp_path / ".env"
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    monkeypatch.setattr("surfaces.shared.llm_setup.env_sync.PROJECT_ENV_PATH", env_path)
    monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", env_path)
    monkeypatch.setattr("config.setup_store.get_store_path", lambda: tmp_path / "opensre.json")
    return env_path


def test_auth_login_deepseek_stores_secret_and_env(monkeypatch, tmp_path: Path) -> None:
    env_path = _patch_auth_env(monkeypatch, tmp_path)

    result = CliRunner().invoke(
        cli,
        [
            "auth",
            "login",
            "deepseek",
            "--api-key",
            "deepseek-secret",
            "--set-provider",
            "--no-validate",
            "--no-open-browser",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Connected: DeepSeek API key" in result.output
    assert resolve_env_credential("DEEPSEEK_API_KEY") == "deepseek-secret"
    env_content = env_path.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=deepseek\n" in env_content
    assert "DEEPSEEK_API_KEY=deepseek-secret\n" in env_content


def test_auth_status_provider_reports_metadata_without_reading_secret(
    monkeypatch, tmp_path: Path
) -> None:
    _patch_auth_env(monkeypatch, tmp_path)
    CliRunner().invoke(
        cli,
        [
            "auth",
            "login",
            "deepseek",
            "--api-key",
            "deepseek-secret",
            "--no-validate",
            "--no-open-browser",
        ],
    )

    result = CliRunner().invoke(cli, ["auth", "status", "deepseek"])

    assert result.exit_code == 0, result.output
    assert "deepseek" in result.output
    assert "ok" in result.output
    assert "deepseek-secret" not in result.output


def test_auth_verify_provider_reports_the_stored_source(monkeypatch, tmp_path: Path) -> None:
    """`opensre auth verify` names the tier that actually holds the secret."""
    _patch_auth_env(monkeypatch, tmp_path)
    CliRunner().invoke(
        cli,
        [
            "auth",
            "login",
            "deepseek",
            "--api-key",
            "deepseek-secret",
            "--no-validate",
            "--no-open-browser",
        ],
    )

    result = CliRunner().invoke(cli, ["auth", "verify", "deepseek"])

    assert result.exit_code == 0, result.output
    assert "Provider : deepseek" in result.output
    assert "Status   : ok" in result.output
    assert "Source   : env" in result.output
    assert "environment" in result.output


def test_subscription_login_rejects_api_keys(monkeypatch, tmp_path: Path) -> None:
    _patch_auth_env(monkeypatch, tmp_path)

    result = CliRunner().invoke(
        cli, ["auth", "login", "chatgpt", "--api-key", "test-secret", "--no-open-browser"]
    )

    assert result.exit_code != 0
    assert "oauth authorization" in result.output
    assert not (tmp_path / "subscriptions").exists()


def test_provider_chooser_defaults_to_the_configured_provider(monkeypatch) -> None:
    """Bare `/auth login` must preselect the install's provider, not the first row."""
    import config.llm_settings as config_module
    from surfaces.shared.llm_setup.auth_menu import configured_profile_name

    monkeypatch.setattr(config_module, "get_configured_llm_provider", lambda: "openai")
    assert configured_profile_name() == "openai"


def test_auth_logout_deepseek_removes_stored_secret(monkeypatch, tmp_path: Path) -> None:
    env_path = _patch_auth_env(monkeypatch, tmp_path)
    CliRunner().invoke(
        cli,
        [
            "auth",
            "login",
            "deepseek",
            "--api-key",
            "deepseek-secret",
            "--no-validate",
            "--no-open-browser",
        ],
    )

    result = CliRunner().invoke(cli, ["auth", "logout", "deepseek"])

    assert result.exit_code == 0, result.output
    assert resolve_env_credential("DEEPSEEK_API_KEY") == ""
    assert "DEEPSEEK_API_KEY=" not in env_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("answers", [[None], ["Subscription", None, None]])
def test_login_picker_cancellation_does_not_start_setup(monkeypatch, answers) -> None:
    from surfaces.shared.llm_setup import auth_menu

    selections = iter(answers)
    monkeypatch.setattr(auth_menu, "configured_profile_name", lambda: "chatgpt")
    monkeypatch.setattr(auth_menu, "repl_choose_subcommand", lambda **_kw: next(selections))
    assert auth_menu.prompt_auth_provider() is None
