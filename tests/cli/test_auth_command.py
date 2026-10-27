from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from config.llm_credentials import resolve_env_credential
from surfaces.cli.app import cli


def test_login_uses_readable_auth_selectors(monkeypatch) -> None:
    from surfaces.shared.llm_setup import auth_menu as auth

    seen = []

    def build(values, **kwargs):
        fields = kwargs["fields"](values)
        seen.extend(fields[0].choices)
        assert kwargs["submit_on_choice"] is True
        return object()

    monkeypatch.setattr(auth, "build_input_form", build)
    monkeypatch.setattr(auth, "run_command_input", lambda _: "chatgpt")
    assert auth.prompt_auth_provider().name == "chatgpt"
    labels = [choice.label for choice in seen]
    assert any("Subscription" in label for label in labels)
    assert any("API billing" in label for label in labels)
    assert any("CLI-managed" in label for label in labels)


def test_subscription_model_selector_can_cancel(monkeypatch) -> None:
    from rich.console import Console

    from surfaces.shared.llm_setup import model_picker

    monkeypatch.setattr(model_picker, "run_command_input", lambda _: None)
    monkeypatch.setattr(model_picker, "model_choices", lambda **_: ())
    assert model_picker.model_picker(Console(), provider_id="chatgpt") is None


def _patch_auth_env(monkeypatch, tmp_path: Path) -> Path:
    env_path = tmp_path / ".env"
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    monkeypatch.setattr("surfaces.shared.llm_setup.env_sync.PROJECT_ENV_PATH", env_path)
    monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", env_path)
    monkeypatch.setattr("config.setup_store.get_store_path", lambda: tmp_path / "opensre.json")
    return env_path


def test_non_tty_login_stores_secret_without_activation(monkeypatch, tmp_path: Path) -> None:
    env_path = _patch_auth_env(monkeypatch, tmp_path)

    result = CliRunner().invoke(
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

    assert result.exit_code == 0, result.output
    assert "Connected: DeepSeek API key" in result.output
    assert resolve_env_credential("DEEPSEEK_API_KEY") == "deepseek-secret"
    assert not env_path.exists()
    assert "opensre model set deepseek" in result.output


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
    assert not env_path.exists() or "DEEPSEEK_API_KEY=" not in env_path.read_text(encoding="utf-8")


def test_login_picker_cancellation_does_not_start_setup(monkeypatch) -> None:
    from surfaces.shared.llm_setup import auth_menu

    monkeypatch.setattr(auth_menu, "run_command_input", lambda _: None)
    assert auth_menu.prompt_auth_provider() is None
