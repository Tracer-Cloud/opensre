from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest

from config import setup_store as wizard_store
from config.secrets.store import SecretSaveResult
from surfaces.cli.wizard import azure_openai, components, flow, llm_credential
from surfaces.cli.wizard.probes import ProbeResult
from surfaces.shared.llm_setup.env_sync import sync_provider_env
from surfaces.shared.llm_setup.validation import ValidationResult
from tests.integrations.llm_cli.testing_helpers import write_fake_runnable_cli_bin

# Persisting a secret reports which storage tier accepted it, so stubs have to
# answer that question too — a stub returning ``None`` would make every wizard
# run look like it had fallen back to on-disk storage.
_KEYRING_SAVE = SecretSaveResult("fallback", "stored in the local credentials file.")


def _stub_save(*_args: object, **_kwargs: object) -> SecretSaveResult:
    return _KEYRING_SAVE


def _stub_save_recording(saved: list, provider: str, value: str) -> SecretSaveResult:
    saved.append((provider, value))
    return _KEYRING_SAVE


@pytest.fixture(autouse=True)
def _stub_managed_llm_secret_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wizard flow tests should not touch the developer's real keychain or ``.env``."""
    monkeypatch.setattr(components, "save_api_key", _stub_save)
    monkeypatch.setattr(components, "_write_llm_api_key_to_env", lambda *_a, **_k: None)


@pytest.fixture(autouse=True)
def _stub_llm_credential_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wizard flow tests must never hit live provider endpoints."""
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="stubbed"),
        raising=False,
    )


@pytest.fixture(autouse=True)
def _skip_loop_seeding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flow, "_seed_onboarding_loops", lambda: 0)


def test_run_wizard_focuses_on_llm_setup_only(monkeypatch, tmp_path, capsys) -> None:
    select_responses = iter(["openai", "gpt-5.4-mini"])

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "secret-key"
        return m

    saved: dict[str, object] = {}
    saved_llm_keys: list[tuple[str, str]] = []
    seeded_loops: list[bool] = []

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))

    def _save_local_config(**kwargs):
        saved.update(kwargs)
        return tmp_path / "opensre.json"

    monkeypatch.setattr(flow, "save_local_config", _save_local_config)
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(flow, "_seed_onboarding_loops", lambda: seeded_loops.append(True) or 3)
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved["wizard_mode"] == "quickstart"
    assert saved["provider"] == "openai"
    assert "api_key" not in saved
    assert saved_llm_keys == [("openai", "secret-key")]
    assert seeded_loops == [True]

    output = capsys.readouterr().out
    assert "Complete your setup to get started" in output
    assert "Summary" in output
    assert "2/2" in output
    assert "next" in output
    assert "Done." in output
    assert output.index("Summary") < output.index("Done.")


def testprompt_value_can_signal_wizard_back(monkeypatch) -> None:
    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = None
        return m

    monkeypatch.setattr(components.questionary, "password", _mock_password)

    with pytest.raises(components.WizardBack):
        components.prompt_value("OpenAI API key", secret=True, back_on_cancel=True)


def test_run_wizard_no_saved_provider_shows_selection(monkeypatch, tmp_path) -> None:
    """With no saved config the provider list is shown immediately (no confirm prompt)."""
    select_responses = iter(["openai", "gpt-5.4-mini"])

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "secret-key"
        return m

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(components, "save_credential", _stub_save)

    exit_code = flow.run_wizard()
    assert exit_code == 0


def test_run_wizard_shows_keyring_fix_steps_when_secure_storage_is_unavailable(
    monkeypatch, tmp_path, capsys
) -> None:
    select_responses = iter(["openai", "gpt-5.4-mini"])

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        # ESCAPE at the keychain recovery menu -> "Setup cancelled." -> exit 1.
        m.ask.return_value = None if "What next?" in prompt else next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "secret-key"
        return m

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("Secure local credential storage is unavailable on this machine.")
        ),
    )
    monkeypatch.setattr(
        components,
        "get_keyring_setup_instructions",
        lambda _env_var: (
            "Current keyring backend: keyring.backends.fail.Keyring.",
            "Install it first: sudo apt update && sudo apt install -y gnome-keyring dbus-user-session",
            "Start a D-Bus shell: dbus-run-session -- sh",
        ),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "OpenSRE could not save your API key to secure local storage." in output
    assert "Install it first: sudo apt update && sudo apt install -y gnome-keyring" in output
    assert "dbus-user-session" in output
    assert "Start a D-Bus shell: dbus-run-session -- sh" in output


def test_run_wizard_reuses_saved_defaults_when_user_keeps_provider(monkeypatch, tmp_path) -> None:
    """When provider is already set and user declines to change, saved values are reused."""
    saved: dict[str, object] = {}
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, choices=None, default=None, **_kwargs):
        m = MagicMock()
        prompt = str(_args[0]) if _args else ""
        selected_value = "skip" if "integration" in prompt.lower() else default
        if choices is not None:
            for choice in choices:
                if getattr(choice, "title", None) == selected_value:
                    selected_value = choice.value
                    break
        m.ask.return_value = selected_value
        return m

    def _mock_confirm(*_args, **_kwargs):
        m = MagicMock()
        # "Change provider?" -> No (keep saved openai)
        m.ask.return_value = False
        return m

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        components,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "openai",
                    "model": "gpt-5.4-mini",
                    "api_key_env": "OPENAI_API_KEY",
                    "api_key": "saved-secret",
                }
            },
        },
    )
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(components, "has_llm_api_key", lambda _env: False)

    def _save_local_config(**kwargs):
        saved.update(kwargs)
        return tmp_path / "opensre.json"

    monkeypatch.setattr(flow, "save_local_config", _save_local_config)
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved["wizard_mode"] == "quickstart"
    assert saved["provider"] == "openai"
    assert saved["model"] == "gpt-5.4-mini"
    assert "api_key" not in saved
    assert saved_llm_keys == [("openai", "saved-secret")]


def test_run_wizard_changes_model_when_user_keeps_provider(monkeypatch, tmp_path) -> None:
    """When provider is kept but user opts to change model, the new choice is saved."""
    saved: dict[str, object] = {}
    confirm_prompts: list[str] = []

    def _mock_select(*_args, choices=None, default=None, **_kwargs):
        m = MagicMock()
        prompt = str(_args[0]) if _args else ""
        if "Choose your LLM provider" in prompt:
            m.ask.return_value = "openai"
        elif "Choose OpenAI model" in prompt:
            m.ask.return_value = "gpt-5.4-mini"
        elif "integration" in prompt.lower():
            # Falling through to `default` here picks "grafana local", whose configurator
            # runs `docker compose up -d` and then seeds a *live* Loki over the network —
            # neither of which this model-selection test has any interest in.
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = default
        return m

    def _mock_confirm(*_args, **_kwargs):
        m = MagicMock()
        prompt = str(_args[0]) if _args else ""
        confirm_prompts.append(prompt)
        if "Change provider?" in prompt:
            m.ask.return_value = False
        elif "Change model?" in prompt:
            m.ask.return_value = True
        else:
            m.ask.return_value = False
        return m

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        components,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "openai",
                    "model": "gpt-5.4-mini",
                    "api_key_env": "OPENAI_API_KEY",
                    "api_key": "saved-secret",
                }
            },
        },
    )
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(components, "has_llm_api_key", lambda _env: True)

    def _save_local_config(**kwargs):
        saved.update(kwargs)
        return tmp_path / "opensre.json"

    monkeypatch.setattr(flow, "save_local_config", _save_local_config)
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved["provider"] == "openai"
    assert saved["model"] == "gpt-5.4-mini"
    assert "Change model?" in confirm_prompts


def test_run_wizard_persists_matching_local_config_and_env(monkeypatch, tmp_path) -> None:
    select_responses = iter(["openai", "gpt-5.4-mini"])
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "openai-secret"
        return m

    store_path = tmp_path / "opensre.json"
    env_path = tmp_path / ".env"

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(components, "get_store_path", lambda: store_path)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow,
        "save_local_config",
        lambda **kwargs: wizard_store.save_local_config(path=store_path, **kwargs),
    )
    monkeypatch.setattr(
        flow,
        "sync_provider_env",
        lambda **kwargs: sync_provider_env(env_path=env_path, **kwargs),
    )
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    env_values = env_path.read_text(encoding="utf-8")

    assert payload["wizard"]["mode"] == "quickstart"
    assert payload["targets"]["local"]["provider"] == "openai"
    assert payload["targets"]["local"]["api_key_env"] == "OPENAI_API_KEY"
    assert payload["targets"]["local"]["model_env"] == "OPENAI_REASONING_MODEL"
    assert "api_key" not in payload["targets"]["local"]

    assert "LLM_PROVIDER=openai\n" in env_values
    assert "OPENAI_API_KEY=" not in env_values
    assert saved_llm_keys == [("openai", "openai-secret")]


def test_run_wizard_gemini_cli_skips_api_key_and_runs_cli_onboarding(monkeypatch, tmp_path) -> None:
    select_responses = iter(["gemini-cli", ""])
    saved_llm_keys: list[tuple[str, str]] = []
    cli_onboarding_providers: list[str] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _cli_onboarding(provider, **_kwargs):
        cli_onboarding_providers.append(provider.value)
        return "ok"

    store_path = tmp_path / "opensre.json"
    env_path = tmp_path / ".env"

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(components, "get_store_path", lambda: store_path)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "_run_cli_llm_onboarding", _cli_onboarding)
    monkeypatch.setattr(
        flow,
        "save_local_config",
        lambda **kwargs: wizard_store.save_local_config(path=store_path, **kwargs),
    )
    monkeypatch.setattr(
        flow,
        "sync_provider_env",
        lambda **kwargs: sync_provider_env(env_path=env_path, **kwargs),
    )
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert cli_onboarding_providers == ["gemini-cli"]
    assert saved_llm_keys == []

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    env_values = env_path.read_text(encoding="utf-8")
    assert payload["targets"]["local"]["provider"] == "gemini-cli"
    assert payload["targets"]["local"]["api_key_env"] == ""
    assert payload["targets"]["local"]["model_env"] == "GEMINI_CLI_MODEL"
    assert "LLM_PROVIDER=gemini-cli\n" in env_values
    assert "GEMINI_CLI_MODEL=\n" in env_values


def test_run_cli_llm_onboarding_ok_when_logged_in(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.name = "codex"
    adapter.binary_env_key = "CODEX_BIN"
    adapter.install_hint = "npm i -g @openai/codex"
    adapter.auth_hint = "Run: codex login"
    adapter.detect.return_value = MagicMock(
        installed=True,
        logged_in=True,
        detail="Logged in.",
    )
    provider = MagicMock()
    provider.label = "OpenAI Codex CLI"
    provider.adapter_factory = lambda: adapter

    result = flow._run_cli_llm_onboarding(provider)
    assert result == "ok"
    adapter.detect.assert_called_once()


def test_run_cli_llm_onboarding_repick_when_not_logged_in(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.name = "codex"
    adapter.binary_env_key = "CODEX_BIN"
    adapter.install_hint = "npm i -g @openai/codex"
    adapter.auth_hint = "Run: codex login"
    adapter.detect.return_value = MagicMock(
        installed=True,
        logged_in=False,
        detail="Not logged in.",
    )
    provider = MagicMock()
    provider.label = "OpenAI Codex CLI"
    provider.adapter_factory = lambda: adapter

    monkeypatch.setattr(flow, "choose", lambda *_args, **_kwargs: "repick")
    result = flow._run_cli_llm_onboarding(provider)
    assert result == "repick"


def test_run_cli_llm_onboarding_ok_after_login_retry(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.name = "codex"
    adapter.binary_env_key = "CODEX_BIN"
    adapter.install_hint = "npm i -g @openai/codex"
    adapter.auth_hint = "Run: codex login"
    detect_calls: list[object] = []

    def _detect():
        detect_calls.append(None)
        if len(detect_calls) == 1:
            return MagicMock(
                installed=True,
                logged_in=False,
                detail="Not logged in.",
            )
        return MagicMock(installed=True, logged_in=True, detail="Logged in.")

    adapter.detect = _detect
    provider = MagicMock()
    provider.label = "OpenAI Codex CLI"
    provider.adapter_factory = lambda: adapter

    monkeypatch.setattr(flow, "choose", lambda *_args, **_kwargs: "retry")
    result = flow._run_cli_llm_onboarding(provider)
    assert result == "ok"
    assert len(detect_calls) == 2


def test_run_cli_llm_onboarding_repick_when_auth_status_unclear(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.name = "codex"
    adapter.binary_env_key = "CODEX_BIN"
    adapter.install_hint = "npm i -g @openai/codex"
    adapter.auth_hint = "Run: codex login"
    adapter.detect.return_value = MagicMock(
        installed=True,
        logged_in=None,
        detail="Auth status unknown.",
    )
    provider = MagicMock()
    provider.label = "OpenAI Codex CLI"
    provider.adapter_factory = lambda: adapter

    monkeypatch.setattr(flow, "choose", lambda *_args, **_kwargs: "repick")
    result = flow._run_cli_llm_onboarding(provider)
    assert result == "repick"


def test_run_cli_llm_onboarding_ok_after_unclear_auth_retry(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.name = "codex"
    adapter.binary_env_key = "CODEX_BIN"
    adapter.install_hint = "npm i -g @openai/codex"
    adapter.auth_hint = "Run: codex login"
    detect_calls: list[object] = []

    def _detect():
        detect_calls.append(None)
        if len(detect_calls) == 1:
            return MagicMock(
                installed=True,
                logged_in=None,
                detail="Auth status unknown.",
            )
        return MagicMock(installed=True, logged_in=True, detail="Logged in.")

    adapter.detect = _detect
    provider = MagicMock()
    provider.label = "OpenAI Codex CLI"
    provider.adapter_factory = lambda: adapter

    monkeypatch.setattr(flow, "choose", lambda *_args, **_kwargs: "retry")
    result = flow._run_cli_llm_onboarding(provider)
    assert result == "ok"
    assert len(detect_calls) == 2


def test_run_cli_llm_onboarding_repick_when_user_chooses_repick(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.name = "codex"
    adapter.binary_env_key = "CODEX_BIN"
    adapter.install_hint = "npm i -g @openai/codex"
    adapter.auth_hint = "Run: codex login"
    adapter.detect.return_value = MagicMock(
        installed=False,
        logged_in=None,
        detail="Not found.",
    )
    provider = MagicMock()
    provider.label = "OpenAI Codex CLI"
    provider.adapter_factory = lambda: adapter

    monkeypatch.setattr(flow, "choose", lambda *_args, **_kwargs: "repick")
    result = flow._run_cli_llm_onboarding(provider)
    assert result == "repick"


def test_run_cli_llm_onboarding_path_override_then_ok(monkeypatch, tmp_path) -> None:
    fake_bin = write_fake_runnable_cli_bin(tmp_path, "codex")

    adapter = MagicMock()
    adapter.name = "codex"
    adapter.binary_env_key = "CODEX_BIN"
    adapter.install_hint = "npm i -g @openai/codex"
    adapter.auth_hint = "Run: codex login"

    detect_calls: list[object] = []

    def _detect():
        detect_calls.append(None)
        if len(detect_calls) == 1:
            return MagicMock(installed=False, logged_in=None, detail="Not found.")
        return MagicMock(installed=True, logged_in=True, detail="Logged in.")

    adapter.detect = _detect
    provider = MagicMock()
    provider.label = "OpenAI Codex CLI"
    provider.adapter_factory = lambda: adapter

    monkeypatch.setattr(flow, "choose", lambda *_args, **_kwargs: "path")
    monkeypatch.setattr(flow, "prompt_value", lambda *_args, **_kwargs: str(fake_bin))
    monkeypatch.setattr(flow, "sync_env_values", lambda *_args, **_kwargs: None)

    original_codex_bin = os.environ.get("CODEX_BIN")
    try:
        result = flow._run_cli_llm_onboarding(provider)

        assert result == "ok"
        assert len(detect_calls) == 2
        # os.environ must be updated in-process so the next detect() call in the
        # retry loop resolves the new binary without a process restart.
        assert os.environ.get("CODEX_BIN") == str(fake_bin)
    finally:
        if original_codex_bin is None:
            os.environ.pop("CODEX_BIN", None)
        else:
            os.environ["CODEX_BIN"] = original_codex_bin


def test_run_cli_llm_onboarding_abort_after_max_retries(monkeypatch) -> None:
    adapter = MagicMock()
    adapter.name = "codex"
    adapter.binary_env_key = "CODEX_BIN"
    adapter.install_hint = "npm i -g @openai/codex"
    adapter.auth_hint = "Run: codex login"
    adapter.detect.return_value = MagicMock(
        installed=False,
        logged_in=None,
        detail="Not found.",
    )
    provider = MagicMock()
    provider.label = "OpenAI Codex CLI"
    provider.adapter_factory = lambda: adapter

    monkeypatch.setattr(flow, "choose", lambda *_args, **_kwargs: "retry")
    result = flow._run_cli_llm_onboarding(provider)
    assert result == "abort"
    assert adapter.detect.call_count == 10


def test_credential_line_for_saved_summary_cli_codex() -> None:
    from surfaces.shared.llm_setup import catalog as wizard_config

    codex = next(p for p in wizard_config.SUPPORTED_PROVIDERS if p.value == "codex")
    assert (
        llm_credential._credential_line_for_saved_summary(codex)
        == "OpenAI Codex CLI (Run: codex login)"
    )


def test_credential_line_for_saved_summary_cli_claude_code() -> None:
    from surfaces.shared.llm_setup import catalog as wizard_config

    claude_code = next(p for p in wizard_config.SUPPORTED_PROVIDERS if p.value == "claude-code")
    assert llm_credential._credential_line_for_saved_summary(claude_code) == (
        "Anthropic Claude Code CLI (Run: claude auth login or set ANTHROPIC_API_KEY)"
    )


def test_credential_line_for_saved_summary_cli_gemini_cli() -> None:
    from surfaces.shared.llm_setup import catalog as wizard_config

    gemini_cli = next(p for p in wizard_config.SUPPORTED_PROVIDERS if p.value == "gemini-cli")
    assert llm_credential._credential_line_for_saved_summary(gemini_cli) == (
        "Google Gemini CLI (Run: gemini (interactive login) or set GEMINI_API_KEY)"
    )


def test_credential_line_for_saved_summary_cli_copilot() -> None:
    from surfaces.shared.llm_setup import catalog as wizard_config

    copilot = next(p for p in wizard_config.SUPPORTED_PROVIDERS if p.value == "copilot")
    line = llm_credential._credential_line_for_saved_summary(copilot)
    # PR #1533: hint surfaces both CLI paths (`copilot login`, `gh auth login`)
    # before the env-var bypass, matching the new CLI-first probe order.
    assert line.startswith("GitHub Copilot CLI (Run `copilot login`")
    assert "gh auth login" in line
    assert "COPILOT_GITHUB_TOKEN" in line


def test_credential_line_for_saved_summary_anthropic() -> None:
    from surfaces.shared.llm_setup import catalog as wizard_config

    anthropic = next(p for p in wizard_config.SUPPORTED_PROVIDERS if p.value == "anthropic")
    assert (
        llm_credential._credential_line_for_saved_summary(anthropic)
        == "local credentials file (~/.opensre/credentials.json)"
    )


def test_credential_line_for_saved_summary_cli_without_factory() -> None:
    from surfaces.shared.llm_setup.catalog import ModelOption, ProviderOption

    p = ProviderOption(
        value="codex",
        label="Fake CLI",
        group="Local CLI providers",
        api_key_env="",
        model_env="CODEX_MODEL",
        default_model="",
        models=(ModelOption(value="", label="default"),),
        credential_kind="cli",
        adapter_factory=None,
        allow_custom_models=True,
    )
    assert llm_credential._credential_line_for_saved_summary(p) == "Fake CLI (CLI)"


def test_run_wizard_switches_provider_and_keeps_store_and_env_in_sync(
    monkeypatch, tmp_path
) -> None:
    # Saved: anthropic. User says yes to "Change provider?" and picks openai.
    select_responses = iter(["openai", "gpt-5.4-mini"])
    confirm_responses = iter([True])  # "Change provider?" -> Yes
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_confirm(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(confirm_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "fresh-openai-key"
        return m

    store_path = tmp_path / "opensre.json"
    env_path = tmp_path / ".env"
    wizard_store.save_local_config(
        wizard_mode="quickstart",
        provider="anthropic",
        model="claude-opus-4-5",
        api_key_env="ANTHROPIC_API_KEY",
        model_env="ANTHROPIC_MODEL",
        probes={
            "local": {"target": "local", "reachable": True, "detail": "ok"},
            "remote": {"target": "remote", "reachable": False, "detail": "down"},
        },
        path=store_path,
    )
    env_path.write_text(
        "LLM_PROVIDER=anthropic\n"
        "ANTHROPIC_API_KEY=saved-anthropic-key\n"
        "ANTHROPIC_MODEL=claude-opus-4-5\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(components, "get_store_path", lambda: store_path)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow,
        "save_local_config",
        lambda **kwargs: wizard_store.save_local_config(path=store_path, **kwargs),
    )
    monkeypatch.setattr(
        flow,
        "sync_provider_env",
        lambda **kwargs: sync_provider_env(env_path=env_path, **kwargs),
    )
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    env_values = env_path.read_text(encoding="utf-8")

    assert payload["targets"]["local"]["provider"] == "openai"
    assert payload["targets"]["local"]["api_key_env"] == "OPENAI_API_KEY"
    assert payload["targets"]["local"]["model_env"] == "OPENAI_REASONING_MODEL"
    assert "api_key" not in payload["targets"]["local"]

    assert "LLM_PROVIDER=openai\n" in env_values
    assert "OPENAI_API_KEY=" not in env_values
    assert "ANTHROPIC_API_KEY=saved-anthropic-key\n" in env_values
    assert "OPENAI_REASONING_MODEL=" in env_values
    assert saved_llm_keys == [("openai", "fresh-openai-key")]


def test_persist_llm_credential_host_kind_writes_env_not_keyring(monkeypatch, tmp_path) -> None:
    """#3291: a host credential lands where the runtime reads it (.env), never the keyring."""
    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE

    synced: dict[str, str] = {}
    monkeypatch.setattr(
        llm_credential, "sync_env_values", lambda values: synced.update(values) or tmp_path / ".env"
    )
    keyring_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        llm_credential,
        "persist_llm_api_key",
        lambda env, val: keyring_calls.append((env, val)) or True,
    )
    monkeypatch.setenv("OLLAMA_HOST", "sentinel-before")

    assert llm_credential._persist_llm_credential(
        PROVIDER_BY_VALUE["ollama"], "http://10.0.0.5:11434"
    )

    assert synced == {"OLLAMA_HOST": "http://10.0.0.5:11434"}
    assert keyring_calls == []
    assert os.environ["OLLAMA_HOST"] == "http://10.0.0.5:11434"


def test_persist_llm_credential_secret_kind_keeps_keyring(monkeypatch, tmp_path) -> None:
    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE

    monkeypatch.setattr(
        llm_credential, "sync_env_values", lambda _values: pytest.fail("secret must not hit .env")
    )
    keyring_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        llm_credential,
        "persist_llm_api_key",
        lambda env, val: keyring_calls.append((env, val)) or True,
    )

    assert llm_credential._persist_llm_credential(PROVIDER_BY_VALUE["anthropic"], "sk-test")

    assert keyring_calls == [("ANTHROPIC_API_KEY", "sk-test")]


def testlocal_defaults_host_kind_ignores_stale_keyring_entry(monkeypatch, tmp_path) -> None:
    """#3291 trap half: a keyring-only host must NOT read as configured — the runtime
    cannot see it, so the wizard must re-prompt instead of skipping."""
    store = tmp_path / "opensre.json"
    store.write_text(json.dumps({"targets": {"local": {"provider": "ollama"}}}))
    monkeypatch.setattr(components, "get_store_path", lambda: store)
    monkeypatch.setattr(components, "has_llm_api_key", lambda _env: True)  # stale keyring entry

    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert components.local_defaults()["has_api_key"] is False

    monkeypatch.setenv("OLLAMA_HOST", "http://10.0.0.5:11434")
    assert components.local_defaults()["has_api_key"] is True


def test_run_wizard_host_kind_does_not_migrate_legacy_api_key(monkeypatch, tmp_path) -> None:
    """#3291 (Greptile P1): a saved Ollama config carrying a stale legacy ``api_key`` must NOT
    have that value migrated into ``OLLAMA_HOST`` — a host is not a secret api key, and writing a
    secret-shaped legacy value to ``.env`` would both leak it in plaintext and point the runtime
    at a bogus host. The wizard must fall through to the host-URL prompt instead."""
    store = tmp_path / "opensre.json"
    store.write_text(
        json.dumps(
            {
                "targets": {
                    "local": {
                        "provider": "ollama",
                        "model": "llama3.1",
                        "auth_method": "api_key",
                        "api_key": "sk-stale-legacy-secret",
                    }
                }
            }
        )
    )
    monkeypatch.setattr(components, "get_store_path", lambda: store)
    monkeypatch.setattr(flow, "get_store_path", lambda: store)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setattr(flow, "probe_local_target", lambda _p: ProbeResult("local", True, "ok"))

    def _mock_select(*_args, **_kwargs):  # quickstart setup mode
        m = MagicMock()
        m.ask.return_value = "quickstart"
        return m

    def _mock_confirm(*_args, **_kwargs):  # "Change provider?" -> No: keep the saved ollama
        m = MagicMock()
        m.ask.return_value = False
        return m

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)

    prompted: list[str] = []

    def _fake_prompt(label, *_args, **_kwargs):
        prompted.append(label)
        return "http://10.0.0.9:11434"  # the host the user actually enters

    monkeypatch.setattr(llm_credential, "prompt_value", _fake_prompt)

    persisted: list[tuple[str, str]] = []

    def _fake_persist(provider, value):
        persisted.append((provider.value, value))
        return False  # stop the wizard right after the credential decision

    monkeypatch.setattr(llm_credential, "_persist_llm_credential", _fake_persist)

    exit_code = flow.run_wizard()

    # The stale legacy api_key was never routed into OLLAMA_HOST; the wizard prompted for the
    # host and would persist the entered value, not the legacy secret.
    assert prompted, "a host-kind provider must fall through to the host-URL prompt"
    assert ("ollama", "sk-stale-legacy-secret") not in persisted
    assert persisted == [("ollama", "http://10.0.0.9:11434")]
    assert exit_code == 1  # _fake_persist returned False to short-circuit the run


def test_run_wizard_llm_key_retries_on_validation_failure(monkeypatch, tmp_path, capsys) -> None:
    """Two consecutive LLM key validation failures, then success.

    Mirrors test_run_wizard_dagster_retries_on_validation_failure: the wizard
    recovers from N consecutive failures and only the final validated key
    reaches the persistence layer.
    """
    menu_responses = iter(["retry", "retry"])
    password_responses = iter(["sk-bad-1", "sk-bad-2", "sk-good"])
    validator_calls: list[tuple[str, str, str]] = []
    saved_llm_keys: list[tuple[str, str]] = []
    call_order: list[str] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _validate(*, provider, api_key, model):
        validator_calls.append((provider.value, api_key, model))
        call_order.append(f"validate:{api_key}")
        if len(validator_calls) < 3:
            return ValidationResult(ok=False, detail="Anthropic rejected the API key.")
        return ValidationResult(ok=True, detail="Anthropic API key validated.")

    def _save_api_key(provider, value, **_kwargs):
        saved_llm_keys.append((provider, value))
        call_order.append(f"persist:{value}")
        return _KEYRING_SAVE

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(components, "save_api_key", _save_api_key)

    exit_code = flow.run_wizard()

    assert validator_calls == [
        ("anthropic", "sk-bad-1", "claude-opus-4-7"),
        ("anthropic", "sk-bad-2", "claude-opus-4-7"),
        ("anthropic", "sk-good", "claude-opus-4-7"),
    ]
    assert saved_llm_keys == [("anthropic", "sk-good")]
    assert call_order[-2:] == ["validate:sk-good", "persist:sk-good"]
    assert exit_code == 0
    output = capsys.readouterr().out
    assert output.count("Anthropic rejected the API key.") >= 2
    assert output.count("Failed") >= 2
    assert "Summary" in output
    assert "Done." in output


def test_run_wizard_saved_provider_missing_key_validates_on_reentry(monkeypatch, tmp_path) -> None:
    """The saved-provider re-entry key prompt validates with the saved model."""
    menu_responses = iter(["retry"])
    password_responses = iter(["sk-bad", "sk-good"])
    validator_calls: list[tuple[str, str, str]] = []
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_confirm(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = False
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _validate(*, provider, api_key, model):
        validator_calls.append((provider.value, api_key, model))
        if len(validator_calls) == 1:
            return ValidationResult(ok=False, detail="OpenAI rejected the API key.")
        return ValidationResult(ok=True, detail="OpenAI API key validated.")

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        components,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "openai",
                    # Deliberately NOT the openai default model: proves the saved
                    # model (not default_model) reaches the validator on this path.
                    "model": "gpt-5.3-mini",
                    "api_key_env": "OPENAI_API_KEY",
                    "auth_method": "api_key",
                }
            },
        },
    )
    monkeypatch.setattr(components, "has_llm_api_key", lambda _env: False)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert validator_calls == [
        ("openai", "sk-bad", "gpt-5.3-mini"),
        ("openai", "sk-good", "gpt-5.3-mini"),
    ]
    assert saved_llm_keys == [("openai", "sk-good")]
    assert exit_code == 0


def test_run_wizard_llm_key_save_anyway_persists_unvalidated_key(
    monkeypatch, tmp_path, capsys
) -> None:
    """Offline escape hatch: save-anyway persists the key without re-validating."""
    menu_responses = iter(["save_anyway"])
    validation_call_count = 0
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-offline"
        return m

    def _validate(*, provider, api_key, model):
        nonlocal validation_call_count
        validation_call_count += 1
        return ValidationResult(ok=False, detail="Validation request failed: network unreachable")

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert validation_call_count == 1
    assert saved_llm_keys == [("anthropic", "sk-offline")]
    output = capsys.readouterr().out
    assert "Saved ANTHROPIC_API_KEY without validating it." in output
    assert exit_code == 0


def test_run_wizard_llm_key_repick_returns_to_provider_menu(monkeypatch, tmp_path) -> None:
    """Repick from a failed key re-renders the provider menu; nothing persisted."""
    select_prompts: list[str] = []
    provider_responses = iter(["anthropic", "openai"])
    menu_responses = iter(["repick"])
    password_responses = iter(["sk-fail-anthropic", "sk-openai-good"])
    validator_calls: list[tuple[str, str]] = []
    saved_llm_keys: list[tuple[str, str]] = []
    synced_env_values: list[dict[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        select_prompts.append(prompt)
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = next(provider_responses)
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "gpt-5.4-mini" if "OpenAI" in prompt else "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _validate(*, provider, api_key, model):
        validator_calls.append((provider.value, api_key))
        if len(validator_calls) == 1:
            return ValidationResult(ok=False, detail="Anthropic rejected the API key.")
        return ValidationResult(ok=True, detail="OpenAI API key validated.")

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        llm_credential,
        "sync_env_values",
        lambda values, **_kwargs: synced_env_values.append(values) or (tmp_path / ".env"),
    )
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )
    exit_code = flow.run_wizard()

    assert select_prompts.count("Choose your LLM provider") == 2
    assert saved_llm_keys == [("openai", "sk-openai-good")]
    assert all("sk-fail-anthropic" not in str(values) for values in synced_env_values)
    assert exit_code == 0


def test_run_wizard_llm_key_valid_first_try_validates_once(monkeypatch, tmp_path, capsys) -> None:
    """Happy path: exactly one validation, before persist, and no new prompts."""
    select_responses = iter(["anthropic", "claude-opus-4-7"])
    validation_call_count = 0
    saved_llm_keys: list[tuple[str, str]] = []
    call_order: list[str] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-good"
        return m

    def _validate(*, provider, api_key, model):
        nonlocal validation_call_count
        validation_call_count += 1
        call_order.append(f"validate:{api_key}")
        return ValidationResult(ok=True, detail="Anthropic API key validated.")

    def _save_api_key(provider, value, **_kwargs):
        saved_llm_keys.append((provider, value))
        call_order.append(f"persist:{value}")
        return _KEYRING_SAVE

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(components, "save_api_key", _save_api_key)

    exit_code = flow.run_wizard()

    assert validation_call_count == 1
    assert saved_llm_keys == [("anthropic", "sk-good")]
    assert call_order == ["validate:sk-good", "persist:sk-good"]
    assert next(select_responses, None) is None  # no leftover prompt: no new menus
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Summary" in output
    assert "Done." in output
    assert output.index("Summary") < output.index("Done.")


def test_run_wizard_azure_llm_key_validation_runs_after_endpoint_prompt(
    monkeypatch, tmp_path
) -> None:
    """Azure: AZURE_OPENAI_BASE_URL must be populated BEFORE the validator runs."""
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "wiped")
    monkeypatch.delenv("AZURE_OPENAI_BASE_URL")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "wiped")
    monkeypatch.delenv("AZURE_OPENAI_API_VERSION")

    endpoint_env_at_validation: list[str | None] = []
    validator_calls: list[tuple[str, str]] = []
    saved_llm_keys: list[tuple[str, str]] = []
    synced_provider_env: list[dict[str, object]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "Choose your LLM provider" in prompt:
            m.ask.return_value = "azure-openai"
        elif "deployment" in prompt.lower() or "model" in prompt:
            m.ask.return_value = "gpt-5.4-mini"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "az-key"
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "https://myres.openai.azure.com"
        return m

    def _validate(*, provider, api_key, model):
        validator_calls.append((provider.value, api_key))
        endpoint_env_at_validation.append(os.environ.get("AZURE_OPENAI_BASE_URL"))
        return ValidationResult(ok=True, detail="Azure OpenAI API key validated.")

    def _sync_provider_env(**kwargs):
        synced_provider_env.append(kwargs)
        return tmp_path / ".env"

    # Azure deployment discovery calls the live resource; stub it so the picker offers
    # the deployment as a menu choice instead of hitting the network (#4117).
    monkeypatch.setattr(
        azure_openai, "discover_azure_openai_deployments_from_env", lambda: ["gpt-5.4-mini"]
    )
    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", _sync_provider_env)
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert endpoint_env_at_validation == ["https://myres.openai.azure.com"]
    assert validator_calls == [("azure-openai", "az-key")]
    assert saved_llm_keys == [("azure-openai", "az-key")]
    extra_env = synced_provider_env[0]["extra_env"]
    assert isinstance(extra_env, dict)
    assert extra_env["AZURE_OPENAI_BASE_URL"] == "https://myres.openai.azure.com"
    assert exit_code == 0


def test_run_wizard_cli_provider_skips_llm_key_validation(monkeypatch, tmp_path) -> None:
    """Scope gate: a CLI-kind provider never triggers key validation.

    The api_key provider tried (and failed) first in the same run proves the
    validator is live and the repick landed on the CLI path — killing the
    tautology where "never validates" holds only because nothing validates.
    """
    provider_responses = iter(["anthropic", "claude-code"])
    menu_responses = iter(["repick"])
    validator_calls: list[tuple[str, str]] = []
    saved_llm_keys: list[tuple[str, str]] = []
    cli_onboarding_providers: list[str] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = next(provider_responses)
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = ""
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-bad-anthropic"
        return m

    def _validate(*, provider, api_key, model):
        validator_calls.append((provider.value, api_key))
        return ValidationResult(ok=False, detail="Anthropic rejected the API key.")

    def _cli_onboarding(provider, **_kwargs):
        cli_onboarding_providers.append(provider.value)
        return "ok"

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(flow, "_run_cli_llm_onboarding", _cli_onboarding)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert validator_calls == [("anthropic", "sk-bad-anthropic")]
    assert cli_onboarding_providers == ["claude-code"]
    assert saved_llm_keys == []
    assert exit_code == 0


def test_run_wizard_ollama_host_is_validated_then_persisted_to_dotenv(
    monkeypatch, tmp_path
) -> None:
    """A host-kind credential (Ollama) IS probed — and still lands in .env, not the keyring.

    Ollama is the one provider whose credential is a host URL rather than a secret, and
    it is exactly the provider a live probe helps most: an unreachable host or an
    un-pulled model is otherwise only discovered later, at the first investigation.
    ``validate_provider_credentials`` already routes ``ollama`` to ``_check_ollama``.

    The api_key provider that validated (and failed) first in the run proves the
    validator is live; the repicked Ollama host is then probed against the model the
    user actually picked and persisted to .env, with nothing written to the keyring.
    """
    provider_responses = iter(["anthropic", "ollama"])
    menu_responses = iter(["repick"])
    validator_calls: list[tuple[str, str, str]] = []
    saved_llm_keys: list[tuple[str, str]] = []
    synced_env_values: list[dict[str, str]] = []

    monkeypatch.setenv("OLLAMA_HOST", "sentinel-before")

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = next(provider_responses)
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "llama3.2" if "Ollama" in prompt else "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-bad-anthropic"
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "http://127.0.0.1:11434"
        return m

    def _validate(*, provider, api_key, model):
        validator_calls.append((provider.value, api_key, model))
        if provider.value == "ollama":
            return ValidationResult(ok=True, detail=f"Ollama reachable. Model '{model}' is ready.")
        return ValidationResult(ok=False, detail="Anthropic rejected the API key.")

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        llm_credential,
        "sync_env_values",
        lambda values, **_kwargs: synced_env_values.append(values) or (tmp_path / ".env"),
    )
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert validator_calls == [
        ("anthropic", "sk-bad-anthropic", "claude-opus-4-7"),
        ("ollama", "http://127.0.0.1:11434", "llama3.2"),
    ]
    assert synced_env_values == [{"OLLAMA_HOST": "http://127.0.0.1:11434"}]
    assert os.environ["OLLAMA_HOST"] == "http://127.0.0.1:11434"
    assert saved_llm_keys == []  # a host URL is config, never a keyring secret
    assert exit_code == 0


def test_run_wizard_ollama_host_env_write_failure_offers_recovery_menu(
    monkeypatch, tmp_path
) -> None:
    """#3591 (cerencamkiran): a host credential whose .env write fails reaches the shared
    recovery menu, it does not crash the wizard.

    ``_persist_llm_credential``'s host branch writes the Ollama host to ``.env`` via
    ``sync_env_values``. If that write raises (permission denied, read-only fs, a full
    disk — any ``OSError``), the user must see the same Retry / Continue-without-saving
    / Pick-a-different-provider menu the keyring path already offers, and choosing
    "Continue without saving (this session only)" must finish onboarding with the host
    exported to ``os.environ`` only — never with the write error propagating.

    Today the host branch does not catch the write error, so the ``OSError`` escapes
    ``_persist_llm_credential_with_recovery`` and crashes onboarding. This test is RED
    until that gap is closed.
    """
    menu_responses = iter(["continue_unsaved"])
    recovery_prompts: list[str] = []
    saved_llm_keys: list[tuple[str, str]] = []

    monkeypatch.setenv("OLLAMA_HOST", "sentinel-before")

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "could not be saved" in prompt:
            recovery_prompts.append(prompt)
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "ollama"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "llama3.2"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "http://127.0.0.1:11434"
        return m

    def _raise_env_write(_values, **_kwargs):
        # Simulate a real .env write failure: no write permission / read-only fs / full disk.
        raise OSError("[Errno 13] Permission denied: '.env'")

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(llm_credential, "sync_env_values", _raise_env_write)
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    # RED expectation: the write error is caught and turned into the shared recovery menu,
    # not propagated. Today ``run_wizard`` raises the OSError here instead.
    exit_code = flow.run_wizard()

    assert recovery_prompts, "a failed .env write must surface the shared recovery menu"
    # "Continue without saving (this session only)" exports the host process-locally only.
    assert os.environ["OLLAMA_HOST"] == "http://127.0.0.1:11434"
    assert saved_llm_keys == []  # a host URL is config, never a keyring secret
    assert exit_code == 0


def test_run_wizard_saved_provider_with_stored_key_skips_validation(monkeypatch, tmp_path) -> None:
    """REGRESSION GATE: a stored key means no key prompt and no validator call."""
    password_prompts: list[str] = []
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        m.ask.return_value = "skip" if "integration" in prompt.lower() else "quickstart"
        return m

    def _mock_confirm(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = False
        return m

    def _mock_password(*_args, **_kwargs):
        password_prompts.append(str(_args[0]) if _args else "")
        m = MagicMock()
        m.ask.return_value = "must-not-be-prompted"
        return m

    def _validate(**_kwargs):
        raise AssertionError("validator must not be called for a stored key")

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate, raising=False)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        components,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "openai",
                    "model": "gpt-5.4-mini",
                    "api_key_env": "OPENAI_API_KEY",
                    "auth_method": "api_key",
                }
            },
        },
    )
    monkeypatch.setattr(components, "has_llm_api_key", lambda _env: True)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert password_prompts == []
    assert saved_llm_keys == []


def test_run_wizard_legacy_key_migration_does_not_validate(monkeypatch, tmp_path) -> None:
    """REGRESSION GATE: silent legacy-store migration persists without validating."""
    password_prompts: list[str] = []
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        m.ask.return_value = "skip" if "integration" in prompt.lower() else "quickstart"
        return m

    def _mock_confirm(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = False
        return m

    def _mock_password(*_args, **_kwargs):
        password_prompts.append(str(_args[0]) if _args else "")
        m = MagicMock()
        m.ask.return_value = "must-not-be-prompted"
        return m

    def _validate(**_kwargs):
        raise AssertionError("validator must not be called for a legacy migration")

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate, raising=False)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        components,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "openai",
                    "model": "gpt-5.4-mini",
                    "api_key_env": "OPENAI_API_KEY",
                    "auth_method": "api_key",
                    "api_key": "saved-secret",
                }
            },
        },
    )
    monkeypatch.setattr(components, "has_llm_api_key", lambda _env: False)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert password_prompts == []
    assert saved_llm_keys == [("openai", "saved-secret")]


def test_run_wizard_llm_key_persist_failure_offers_recovery_menu(
    monkeypatch, tmp_path, capsys
) -> None:
    """Keyring persist failure offers retry instead of hard-exiting with 1."""
    select_prompts: list[str] = []
    menu_responses = iter(["retry"])
    validation_call_count = 0
    persist_attempts: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        select_prompts.append(prompt)
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-good"
        return m

    def _validate(*, provider, api_key, model):
        nonlocal validation_call_count
        validation_call_count += 1
        return ValidationResult(ok=True, detail="Anthropic API key validated.")

    def _save_api_key(provider, value, **_kwargs):
        persist_attempts.append((provider, value))
        if len(persist_attempts) == 1:
            raise RuntimeError("keychain locked")
        return _KEYRING_SAVE

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(components, "save_api_key", _save_api_key)
    monkeypatch.setattr(
        components,
        "get_keyring_setup_instructions",
        lambda _env_var: ("Current keyring backend: keyring.backends.fail.Keyring.",),
    )

    exit_code = flow.run_wizard()

    assert persist_attempts == [("anthropic", "sk-good"), ("anthropic", "sk-good")]
    assert exit_code == 0
    assert validation_call_count == 1  # validation is NOT re-run on persist retry
    assert "ANTHROPIC_API_KEY could not be saved. What next?" in select_prompts
    output = capsys.readouterr().out
    assert "OpenSRE could not save your API key to secure local storage." in output


def test_run_wizard_llm_key_persist_failure_repick_returns_to_provider_menu(
    monkeypatch, tmp_path
) -> None:
    """Repick from the persist-failure menu re-renders the provider menu."""
    select_prompts: list[str] = []
    provider_responses = iter(["anthropic", "openai"])
    menu_responses = iter(["repick"])
    validation_call_count = 0
    persist_attempts: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        select_prompts.append(prompt)
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = next(provider_responses)
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "gpt-5.4-mini" if "OpenAI" in prompt else "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    password_responses = iter(["sk-anthropic", "sk-openai"])

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _validate(*, provider, api_key, model):
        nonlocal validation_call_count
        validation_call_count += 1
        return ValidationResult(ok=True, detail="API key validated.")

    def _save_api_key(provider, value, **_kwargs):
        persist_attempts.append((provider, value))
        if len(persist_attempts) == 1:
            raise RuntimeError("keychain locked")
        return _KEYRING_SAVE

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(components, "save_api_key", _save_api_key)
    monkeypatch.setattr(
        components,
        "get_keyring_setup_instructions",
        lambda _env_var: ("Current keyring backend: keyring.backends.fail.Keyring.",),
    )

    exit_code = flow.run_wizard()

    assert select_prompts.count("Choose your LLM provider") == 2
    assert persist_attempts == [("anthropic", "sk-anthropic"), ("openai", "sk-openai")]
    assert validation_call_count == 2
    assert exit_code == 0


def test_run_wizard_llm_key_persist_failure_abort_exits_nonzero(
    monkeypatch, tmp_path, capsys
) -> None:
    """Ctrl+C at the persist-failure menu cancels setup with exit code 1."""
    select_prompts: list[str] = []
    persist_attempts: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        select_prompts.append(prompt)
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = None  # Ctrl+C at the recovery menu
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-good"
        return m

    def _save_api_key(provider, value, **_kwargs):
        persist_attempts.append((provider, value))
        raise RuntimeError("keychain locked")

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="Anthropic API key validated."),
    )
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(components, "save_api_key", _save_api_key)
    monkeypatch.setattr(
        components,
        "get_keyring_setup_instructions",
        lambda _env_var: ("Current keyring backend: keyring.backends.fail.Keyring.",),
    )

    exit_code = flow.run_wizard()

    output = capsys.readouterr().out
    assert "Setup cancelled." in output
    assert exit_code == 1
    assert "ANTHROPIC_API_KEY could not be saved. What next?" in select_prompts
    assert persist_attempts == [("anthropic", "sk-good")]


def test_run_wizard_llm_key_back_at_reprompt_returns_to_provider_menu(
    monkeypatch, tmp_path, capsys
) -> None:
    """WizardBack at the key re-prompt returns to the provider menu, not cancel."""
    select_prompts: list[str] = []
    provider_responses = iter(["anthropic", "openai"])
    menu_responses = iter(["retry"])
    password_responses = iter(["sk-bad", None, "sk-openai-good"])
    validator_calls: list[tuple[str, str]] = []
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        select_prompts.append(prompt)
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = next(provider_responses)
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "gpt-5.4-mini" if "OpenAI" in prompt else "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _validate(*, provider, api_key, model):
        validator_calls.append((provider.value, api_key))
        if len(validator_calls) == 1:
            return ValidationResult(ok=False, detail="Anthropic rejected the API key.")
        return ValidationResult(ok=True, detail="OpenAI API key validated.")

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert select_prompts.count("Choose your LLM provider") == 2
    assert [call[1] for call in validator_calls] == ["sk-bad", "sk-openai-good"]
    assert saved_llm_keys == [("openai", "sk-openai-good")]
    output = capsys.readouterr().out
    assert "Setup cancelled." not in output
    assert exit_code == 0


def test_run_wizard_llm_key_ctrl_c_at_retry_menu_cancels_setup(
    monkeypatch, tmp_path, capsys
) -> None:
    """Ctrl+C at the validation-failure menu cancels setup; nothing persisted."""
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = None  # Ctrl+C at the retry menu
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-bad"
        return m

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=False, detail="Anthropic rejected the API key."),
    )
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "Setup cancelled." in output
    assert saved_llm_keys == []


def test_run_wizard_llm_key_validator_exception_treated_as_failure(
    monkeypatch, tmp_path, capsys
) -> None:
    """A raising validator renders as a failure instead of crashing the wizard."""
    select_prompts: list[str] = []
    provider_responses = iter(["anthropic", "openai"])
    menu_responses = iter(["repick"])
    password_responses = iter(["sk-anthropic", "sk-openai"])
    validation_call_count = 0
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        select_prompts.append(prompt)
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = next(provider_responses)
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "gpt-5.4-mini" if "OpenAI" in prompt else "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _validate(*, provider, api_key, model):
        nonlocal validation_call_count
        validation_call_count += 1
        if validation_call_count == 1:
            raise RuntimeError("boom")
        return ValidationResult(ok=True, detail="OpenAI API key validated.")

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert validation_call_count == 2
    output = capsys.readouterr().out
    assert "boom" in output
    # The raising probe is call #1 — Anthropic — so Anthropic is the provider named in
    # the failure menu. OpenAI is the provider the user repicks, and it validates cleanly.
    assert "Anthropic API key could not be verified. What next?" in select_prompts
    assert saved_llm_keys == [("openai", "sk-openai")]
    assert exit_code == 0


def test_run_wizard_llm_key_validation_failure_output_masks_secret(
    monkeypatch, tmp_path, capsys
) -> None:
    """The fail -> retry -> save-anyway path never leaks the key to any surface."""
    secret = "sk-super-secret-XYZ"
    menu_responses = iter(["retry", "save_anyway"])
    validation_call_count = 0
    saved_llm_keys: list[tuple[str, str]] = []
    password_calls: list[dict[str, object]] = []
    select_renderings: list[str] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        rendered = [prompt]
        for choice in _kwargs.get("choices") or []:
            rendered.append(str(getattr(choice, "title", "")))
            rendered.append(str(getattr(choice, "description", "")))
        select_renderings.append(" ".join(rendered))
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        password_calls.append({"label": str(_args[0]) if _args else "", **_kwargs})
        m = MagicMock()
        m.ask.return_value = secret
        return m

    def _validate(*, provider, api_key, model):
        nonlocal validation_call_count
        validation_call_count += 1
        return ValidationResult(ok=False, detail="Anthropic rejected the API key.")

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert validation_call_count == 2
    captured = capsys.readouterr()
    assert "Saved ANTHROPIC_API_KEY without validating it." in captured.out
    assert secret not in captured.out
    assert secret not in captured.err
    assert all(secret not in str(call.get("default", "")) for call in password_calls)
    assert all(secret not in str(call.get("instruction") or "") for call in password_calls)
    assert all(secret not in rendered for rendered in select_renderings)
    assert saved_llm_keys == [("anthropic", secret)]
    assert exit_code == 0


# ---------------------------------------------------------------------------
# DELTA 3 — validation must probe the model the user actually picked.
#
# Today `run_wizard` prompts for + validates the credential BEFORE `choose_model`
# runs (flow.py: credential block ~920-974, model selection ~976-1003), so the
# validator is handed `model_provider.default_model` (change-provider branch) or
# the *stale* saved model (saved-provider branch) instead of the model that is
# subsequently persisted. GREEN hoists the model selection above the credential
# block in BOTH branches.
# ---------------------------------------------------------------------------


def test_run_wizard_validates_the_model_the_user_picked_change_provider(
    monkeypatch, tmp_path
) -> None:
    """Change-provider branch: the validated model is the PICKED model, not the default.

    Anthropic's ``default_model`` is ``claude-opus-4-7``; the user picks
    ``claude-haiku-4-5``. The credential must be probed against the model that is
    actually written to the store, otherwise a key that is not entitled to the
    default model is reported as invalid.
    """
    observed_models: list[str] = []
    saved: dict[str, object] = {}

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-haiku-4-5"  # NOT the default (claude-opus-4-7)
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-good"
        return m

    def _validate(*, provider, api_key, model):
        observed_models.append(model)
        return ValidationResult(ok=True, detail="Anthropic API key validated.")

    def _save_local_config(**kwargs):
        saved.update(kwargs)
        return tmp_path / "opensre.json"

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", _save_local_config)
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(components, "save_api_key", _stub_save)

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved["model"] == "claude-haiku-4-5"
    # The model that was VALIDATED must be byte-for-byte the model that was PERSISTED.
    assert observed_models == ["claude-haiku-4-5"]
    assert "claude-opus-4-7" not in observed_models


def test_run_wizard_validates_the_model_the_user_picked_saved_provider(
    monkeypatch, tmp_path
) -> None:
    """Saved-provider branch: changing the model must change the model that is validated.

    The user keeps the saved provider (openai), re-enters the key because none is
    stored, and then changes the model from the saved ``gpt-5.4-mini`` to
    ``gpt-5.6-sol``. The probe must use ``gpt-5.6-sol`` — the value that is
    persisted — not the stale saved model.
    """
    observed_models: list[str] = []
    saved: dict[str, object] = {}

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "model" in prompt:
            m.ask.return_value = "gpt-5.6-sol"  # NOT the saved model (gpt-5.4-mini)
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_confirm(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        m.ask.return_value = "Change model?" in prompt  # keep provider, change model
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-good"
        return m

    def _validate(*, provider, api_key, model):
        observed_models.append(model)
        return ValidationResult(ok=True, detail="OpenAI API key validated.")

    def _save_local_config(**kwargs):
        saved.update(kwargs)
        return tmp_path / "opensre.json"

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        components,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "openai",
                    "model": "gpt-5.4-mini",
                    "api_key_env": "OPENAI_API_KEY",
                    "auth_method": "api_key",
                }
            },
        },
    )
    monkeypatch.setattr(components, "has_llm_api_key", lambda _env: False)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", _save_local_config)
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(components, "save_api_key", _stub_save)

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved["model"] == "gpt-5.6-sol"
    assert observed_models == ["gpt-5.6-sol"]
    assert "gpt-5.4-mini" not in observed_models


def test_run_wizard_ollama_validates_the_selected_model_not_the_default(
    monkeypatch, tmp_path
) -> None:
    """LOCKOUT REGRESSION GATE (AC-2): Ollama is probed against the SELECTED model.

    ``_check_ollama`` (validation.py) hard-fails when the probed model is not
    pulled: ``Model '<model>' not found. Run: ollama pull <model>``. If the wizard
    probes ``default_model`` (``llama3.2``) instead of the model the user picked
    (``qwen2.5:7b``), every Ollama user who selects a non-default model is told to
    pull a model they never chose — on every retry, with no way forward.

    NOTE: this asserts Ollama IS validated. The DR copy table contracts the Ollama
    probe explicitly (C1 "Validating Ollama (local) host URL...", C6 "Cannot reach
    Ollama at {host}...", C7 "Ollama (local) host URL could not be verified."), so
    the ``credential_kind == "api_key"`` gate currently guarding the validate call
    must widen to cover the ``host`` kind.
    """
    observed: list[tuple[str, str, str]] = []
    saved: dict[str, object] = {}

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "Choose your LLM provider" in prompt:
            m.ask.return_value = "ollama"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "qwen2.5:7b"  # NOT the default (llama3.2)
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "http://127.0.0.1:11434"
        return m

    def _validate(*, provider, api_key, model):
        observed.append((provider.value, api_key, model))
        return ValidationResult(ok=True, detail="Ollama host URL validated.")

    def _save_local_config(**kwargs):
        saved.update(kwargs)
        return tmp_path / "opensre.json"

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", _save_local_config)
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(llm_credential, "sync_env_values", lambda _values, **_kw: tmp_path / ".env")

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved["model"] == "qwen2.5:7b"
    assert observed == [("ollama", "http://127.0.0.1:11434", "qwen2.5:7b")]
    assert all(model != "llama3.2" for _p, _k, model in observed)


# ---------------------------------------------------------------------------
# DELTA 4 (D2) — ONE shared recovery menu for BOTH failure sites.
#
# Contract (DR menu_contract):
#   _recovery_action(*, prompt, retry_label, retry_hint, escape)
#       -> "retry" | escape.value | "repick" | "cancel"
#   Exactly 3 rows, always in this order:
#       row 1  retry   (THE DEFAULT — value MUST be the literal "retry")
#       row 2  the per-site escape hatch (save_anyway | continue_unsaved)
#       row 3  _REPICK_CHOICE
# ---------------------------------------------------------------------------


def test_recovery_action_renders_three_rows_with_retry_as_the_default(monkeypatch) -> None:
    """The shared menu: 3 rows in a fixed order, row 1 is "retry" AND is the default.

    ``choose(default=...)`` matches by ``Choice.value`` and raises ``ValueError``
    when it matches nothing, so ``default`` and ``choices[0].value`` must both be
    the literal string ``"retry"`` — this is a crash gate, not a cosmetic one.
    """
    recorded: dict[str, object] = {}

    def _fake_choose(prompt, choices, **kwargs):
        recorded["prompt"] = prompt
        recorded["choices"] = choices
        recorded["kwargs"] = kwargs
        return "retry"

    monkeypatch.setattr(llm_credential, "choose", _fake_choose)

    escape = flow.Choice(
        value="save_anyway",
        label="Save anyway without validating",
        hint=(
            "Stores ANTHROPIC_API_KEY unverified — use when you are offline, "
            "proxied, or sure it is correct"
        ),
    )
    action = llm_credential._recovery_action(
        prompt="Anthropic API key could not be verified. What next?",
        retry_label="Re-enter the API key",
        retry_hint="Prompts for ANTHROPIC_API_KEY again",
        escape=escape,
    )

    assert action == "retry"
    assert recorded["prompt"] == "Anthropic API key could not be verified. What next?"

    choices = recorded["choices"]
    assert [choice.value for choice in choices] == ["retry", "save_anyway", "repick"]

    kwargs = recorded["kwargs"]
    assert kwargs["default"] == "retry"
    # The literal "retry" must be row 1's value or questionary raises ValueError.
    assert kwargs["default"] == choices[0].value
    # The WizardBack arm is dead code by contract: choose must not be asked to
    # raise WizardBack here (it only does so when back_on_cancel=True).
    assert kwargs.get("back_on_cancel", False) is False

    assert choices[0].label == "Re-enter the API key"
    assert choices[0].hint == "Prompts for ANTHROPIC_API_KEY again"
    assert choices[1] is escape
    # Row 3's label is byte-identical to the pre-existing recovery menus.
    assert choices[2].label == "Pick a different LLM provider"
    assert choices[2].hint == "Returns to the provider and model picks"


def test_recovery_action_escape_returns_cancel_and_prints_setup_cancelled(
    monkeypatch, capsys
) -> None:
    """ESCAPE at either menu -> "cancel" -> "Setup cancelled." (X1).

    ESCAPE — not Ctrl-C — is the production trigger: ``choose`` is called without
    ``back_on_cancel``, so an escaped select raises a BARE ``KeyboardInterrupt``.
    """

    def _fake_choose(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(llm_credential, "choose", _fake_choose)

    action = llm_credential._recovery_action(
        prompt="ANTHROPIC_API_KEY could not be saved. What next?",
        retry_label="Retry saving to the local credentials file",
        retry_hint="Run the steps above first, then retry",
        escape=flow.Choice(
            value="continue_unsaved",
            label="Continue without saving (this session only)",
            hint="Exports ANTHROPIC_API_KEY for this run — you must re-enter it next time",
        ),
    )

    assert action == "cancel"
    assert "Setup cancelled." in capsys.readouterr().out


def test_run_wizard_keyring_failure_continue_unsaved_exports_env_and_never_writes_dotenv(
    monkeypatch, tmp_path, capsys
) -> None:
    """The keychain escape hatch: export for this session ONLY, never leak to .env.

    ``continue_unsaved`` must set ``os.environ[provider.api_key_env]`` and nothing
    else. Writing the secret to ``.env`` is a secret-leak regression (#3291).
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    select_prompts: list[str] = []
    menu_responses = iter(["continue_unsaved"])
    persist_attempts: list[tuple[str, str]] = []
    synced_env_values: list[dict[str, str]] = []
    env_path = tmp_path / ".env"

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        select_prompts.append(prompt)
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-good"
        return m

    def _save_api_key(provider, value, **_kwargs):
        persist_attempts.append((provider, value))
        raise RuntimeError("keychain locked")

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="Anthropic API key validated."),
    )
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    # Run the REAL sync against a tmp .env so the assertion below exercises the true
    # pop-then-re-apply path: sync_provider_env pops ANTHROPIC_API_KEY from os.environ,
    # and only the #3591 re-apply keeps it alive for the in-process shell handoff.
    monkeypatch.setattr(
        flow,
        "sync_provider_env",
        lambda **kwargs: sync_provider_env(env_path=env_path, **kwargs),
    )
    monkeypatch.setattr(
        llm_credential,
        "sync_env_values",
        lambda values, **_kw: synced_env_values.append(dict(values)) or (tmp_path / ".env"),
    )
    monkeypatch.setattr(components, "save_api_key", _save_api_key)
    monkeypatch.setattr(
        components,
        "get_keyring_setup_instructions",
        lambda _env_var: ("Current keyring backend: keyring.backends.fail.Keyring.",),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert "ANTHROPIC_API_KEY could not be saved. What next?" in select_prompts
    # Exported for this process only, and it SURVIVES the real sync_provider_env pop so
    # the in-process shell handoff can read it (#3591). Without the re-apply this is "".
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-good"
    # ...and NEVER written to .env — assert against the real file the real sync wrote.
    env_written = env_path.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=" not in env_written
    assert "sk-good" not in env_written
    assert all("ANTHROPIC_API_KEY" not in values for values in synced_env_values)
    assert all("sk-good" not in values.values() for values in synced_env_values)
    assert persist_attempts == [("anthropic", "sk-good")]
    output = capsys.readouterr().out
    assert "Using ANTHROPIC_API_KEY for this session only" in output


def test_run_wizard_keyring_failure_at_saved_provider_site_reaches_the_shared_menu(
    monkeypatch, tmp_path
) -> None:
    """Site B / saved-provider re-entry: keychain failure reaches the shared menu.

    "retry" re-runs the WRITE with the same value and does NOT re-prompt for the key.
    """
    select_prompts: list[str] = []
    password_prompts: list[str] = []
    menu_responses = iter(["retry"])
    persist_attempts: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        select_prompts.append(prompt)
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_confirm(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = False  # keep provider, keep model
        return m

    def _mock_password(*_args, **_kwargs):
        password_prompts.append(str(_args[0]) if _args else "")
        m = MagicMock()
        m.ask.return_value = "sk-good"
        return m

    def _save_api_key(provider, value, **_kwargs):
        persist_attempts.append((provider, value))
        if len(persist_attempts) == 1:
            raise RuntimeError("keychain locked")
        return _KEYRING_SAVE

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="Anthropic API key validated."),
    )
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        components,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "anthropic",
                    "model": "claude-opus-4-7",
                    "api_key_env": "ANTHROPIC_API_KEY",
                    "auth_method": "api_key",
                }
            },
        },
    )
    monkeypatch.setattr(components, "has_llm_api_key", lambda _env: False)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(components, "save_api_key", _save_api_key)
    monkeypatch.setattr(
        components,
        "get_keyring_setup_instructions",
        lambda _env_var: ("Current keyring backend: keyring.backends.fail.Keyring.",),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert "ANTHROPIC_API_KEY could not be saved. What next?" in select_prompts
    # "retry" re-attempts the WRITE with the same value — no second key prompt.
    assert persist_attempts == [("anthropic", "sk-good"), ("anthropic", "sk-good")]
    assert len(password_prompts) == 1


def test_run_wizard_keyring_failure_at_legacy_migration_site_reaches_the_shared_menu(
    monkeypatch, tmp_path
) -> None:
    """Site C / legacy-key migration: keychain failure reaches the SAME shared menu.

    This site has no key prompt at all, which is exactly why the shared menu's
    "retry" verb re-runs the WRITE (not the prompt) — it is correct here too.
    """
    select_prompts: list[str] = []
    password_prompts: list[str] = []
    menu_responses = iter(["retry"])
    persist_attempts: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        select_prompts.append(prompt)
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_confirm(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = False
        return m

    def _mock_password(*_args, **_kwargs):
        password_prompts.append(str(_args[0]) if _args else "")
        m = MagicMock()
        m.ask.return_value = "must-not-be-prompted"
        return m

    def _save_api_key(provider, value, **_kwargs):
        persist_attempts.append((provider, value))
        if len(persist_attempts) == 1:
            raise RuntimeError("keychain locked")
        return _KEYRING_SAVE

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="stubbed"),
    )
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        components,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "openai",
                    "model": "gpt-5.4-mini",
                    "api_key_env": "OPENAI_API_KEY",
                    "auth_method": "api_key",
                    "api_key": "saved-secret",
                }
            },
        },
    )
    monkeypatch.setattr(components, "has_llm_api_key", lambda _env: False)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(components, "save_api_key", _save_api_key)
    monkeypatch.setattr(
        components,
        "get_keyring_setup_instructions",
        lambda _env_var: ("Current keyring backend: keyring.backends.fail.Keyring.",),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert "OPENAI_API_KEY could not be saved. What next?" in select_prompts
    assert persist_attempts == [("openai", "saved-secret"), ("openai", "saved-secret")]
    assert password_prompts == []  # the migration site never prompts


# ---------------------------------------------------------------------------
# DELTA 5 (OPEN-K) — the credential prompt must not offer a URL as an API key.
#
# The key prompt passes `default=provider.credential_default`. For azure-openai
# that default is the ENDPOINT placeholder "https://your-resource.openai.azure.com",
# and `prompt_value` RETURNS THE DEFAULT ON EMPTY INPUT (components.py) — so a bare Enter
# silently persists a URL as the API key. Ollama's host default must survive.
# ---------------------------------------------------------------------------


def test_run_wizard_azure_empty_key_input_never_persists_the_endpoint_placeholder(
    monkeypatch, tmp_path
) -> None:
    """Enter on an empty Azure key prompt must NOT save the endpoint placeholder.

    Azure's ``credential_default`` is an endpoint URL, so a pre-filled prompt
    would let a bare Enter persist it as the API key. A blank answer now defers
    setup instead of re-prompting, and must still persist nothing.
    """
    monkeypatch.delenv("AZURE_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_VERSION", raising=False)

    placeholder = flow.PROVIDER_BY_VALUE["azure-openai"].credential_default
    assert placeholder == "https://your-resource.openai.azure.com"  # sanity: the trap exists

    password_calls: list[dict[str, object]] = []
    password_asks = iter([""])  # bare Enter: no key to hand
    validated_keys: list[str] = []
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "Choose your LLM provider" in prompt:
            m.ask.return_value = "azure-openai"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "deployment" in prompt.lower() or "model" in prompt:
            m.ask.return_value = "gpt-5.4-mini"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **kwargs):
        password_calls.append(kwargs)
        m = MagicMock()
        m.ask.return_value = next(password_asks)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "https://myres.openai.azure.com"
        return m

    def _validate(*, provider, api_key, model):
        validated_keys.append(api_key)
        return ValidationResult(ok=True, detail="Azure OpenAI API key validated.")

    monkeypatch.setattr(
        azure_openai, "discover_azure_openai_deployments_from_env", lambda: ["gpt-5.4-mini"]
    )
    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    # The secret prompt must not pre-fill (and therefore must not return) the URL.
    assert all(call.get("default") == "" for call in password_calls)
    assert placeholder not in validated_keys
    assert all(value != placeholder for _provider, value in saved_llm_keys)
    # Deferred: nothing validated, nothing persisted, wizard still completes.
    assert validated_keys == []
    assert saved_llm_keys == []


def test_run_wizard_ollama_host_prompt_keeps_its_localhost_default(monkeypatch, tmp_path) -> None:
    """OVER-FIX GUARD: clearing the prompt default must NOT strip Ollama's host default.

    Deliberately green today. It exists to go RED if DELTA 5 clears the default for
    every credential kind instead of only the non-"host" (secret) kinds: Ollama's
    host URL is plain config, and a bare Enter must still yield
    ``http://localhost:11434``.
    """
    text_calls: list[dict[str, object]] = []
    synced_env_values: list[dict[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "Choose your LLM provider" in prompt:
            m.ask.return_value = "ollama"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "llama3.2"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_text(*_args, **kwargs):
        text_calls.append(kwargs)
        m = MagicMock()
        m.ask.return_value = ""  # bare Enter -> must fall back to the offered default
        return m

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="Ollama host URL validated."),
    )
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        llm_credential,
        "sync_env_values",
        lambda values, **_kw: synced_env_values.append(dict(values)) or (tmp_path / ".env"),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert any(call.get("default") == "http://localhost:11434" for call in text_calls)
    assert synced_env_values == [{"OLLAMA_HOST": "http://localhost:11434"}]


# ---------------------------------------------------------------------------
# DELTA 6 (OPEN-C) — the Summary screen must not contradict its own warning.
#
# `_credential_line_for_saved_summary` must stay honest: after a warning that
# the key was NOT saved, the summary must not claim the credentials file holds it.
# ---------------------------------------------------------------------------


def _summary_kwargs_spy(monkeypatch) -> dict[str, object]:
    """Record the kwargs `render_saved_summary` is called with, then render for real."""
    recorded: dict[str, object] = {}
    real = flow.render_saved_summary

    def _spy(**kwargs):
        recorded.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(flow, "render_saved_summary", _spy)
    return recorded


def test_run_wizard_summary_credential_line_after_continue_unsaved(monkeypatch, tmp_path) -> None:
    """Summary must read "not saved — re-enter next run" after continue_unsaved."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    summary = _summary_kwargs_spy(monkeypatch)
    menu_responses = iter(["continue_unsaved"])

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-good"
        return m

    def _save_api_key(_provider, _value, **_kwargs):
        raise RuntimeError("keychain locked")

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="Anthropic API key validated."),
    )
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(components, "save_api_key", _save_api_key)
    monkeypatch.setattr(
        components,
        "get_keyring_setup_instructions",
        lambda _env_var: ("Current keyring backend: keyring.backends.fail.Keyring.",),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert summary["credential_line"] == "not saved — re-enter next run"


def test_run_wizard_summary_credential_line_after_save_anyway(monkeypatch, tmp_path) -> None:
    """Summary must read "local credentials file (... unverified)" after save_anyway."""
    summary = _summary_kwargs_spy(monkeypatch)
    menu_responses = iter(["save_anyway"])

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            m.ask.return_value = next(menu_responses)
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "anthropic"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "claude-opus-4-7"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "sk-offline"
        return m

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(
            ok=False, detail="Validation request failed: Connection error."
        ),
    )
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(components, "save_api_key", _stub_save)

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert (
        summary["credential_line"]
        == "local credentials file (~/.opensre/credentials.json, unverified)"
    )


# ---------------------------------------------------------------------------
# X2 — `except WizardBack` MUST precede `except KeyboardInterrupt`.
# ---------------------------------------------------------------------------


def test_prompt_validated_llm_credential_wizard_back_precedes_keyboard_interrupt(
    monkeypatch, capsys
) -> None:
    """EXCEPT-ORDER GATE. This test exists to go RED if the two arms are inverted.

    ``WizardBack`` SUBCLASSES ``KeyboardInterrupt`` (components.py), so an
    ``except KeyboardInterrupt`` arm placed FIRST silently swallows back-navigation
    and turns it into "Setup cancelled." Back-out at the key prompt must return
    "repick" (-> the provider picker) and must print nothing about cancelling.
    """

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = None  # ESCAPE at the key prompt -> WizardBack
        return m

    def _must_not_validate(**_kwargs):
        raise AssertionError("validation must not run after a back-out")

    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _must_not_validate)

    provider = flow.PROVIDER_BY_VALUE["anthropic"]
    outcome, _model = llm_credential._prompt_validated_llm_credential(
        provider, model="claude-opus-4-7"
    )

    assert outcome == "repick"
    assert "Setup cancelled." not in capsys.readouterr().out


def test_run_wizard_azure_endpoint_reprompted_on_retry_after_validation_failure(
    monkeypatch, tmp_path
) -> None:
    """#3591 FIX 1 (RED): after an Azure endpoint+key validation FAILURE, choosing
    "retry" must re-prompt the ENDPOINT, not only the key.

    Repro of the dead-end: a wrong-but-valid endpoint is entered, then a key. The
    validator fails. The recovery menu's "retry" arm re-enters only the key; the next
    iteration sees ``azure_openai_endpoint_configured() == True`` (os.environ still
    holds the bad AZURE_OPENAI_BASE_URL) and short-circuits, so the second validation
    reuses the stale bad endpoint and the corrected endpoint the user would type is
    never asked for. The fix must let "retry" re-prompt the endpoint so a corrected
    URL reaches the second validation call.
    """
    # Ensure the Azure endpoint env starts unset AND is restored after the test, even
    # though the wizard writes it into os.environ mid-run.
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "wiped")
    monkeypatch.delenv("AZURE_OPENAI_BASE_URL")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "wiped")
    monkeypatch.delenv("AZURE_OPENAI_API_VERSION")

    endpoint_prompts: list[str] = []
    endpoint_env_at_validation: list[str | None] = []
    validator_calls: list[tuple[str, str]] = []
    saved_llm_keys: list[tuple[str, str]] = []

    endpoint_values = iter(
        [
            "https://wrong-resource.openai.azure.com",
            "https://right-resource.openai.azure.com",
        ]
    )

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            # First (and only) validation failure -> re-enter, staying on Azure.
            m.ask.return_value = "retry"
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = "azure-openai"
        elif "deployment" in prompt.lower() or "model" in prompt:
            m.ask.return_value = "gpt-5.4-mini"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "az-key"
        return m

    def _mock_text(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        endpoint_prompts.append(prompt)
        m = MagicMock()
        m.ask.return_value = next(endpoint_values)
        return m

    def _validate(*, provider, api_key, model):
        validator_calls.append((provider.value, api_key))
        endpoint_env_at_validation.append(os.environ.get("AZURE_OPENAI_BASE_URL"))
        # First attempt fails; the retry (with a corrected endpoint) must succeed.
        if len(validator_calls) == 1:
            return ValidationResult(ok=False, detail="Azure OpenAI rejected the request.")
        return ValidationResult(ok=True, detail="Azure OpenAI API key validated.")

    monkeypatch.setattr(
        azure_openai, "discover_azure_openai_deployments_from_env", lambda: ["gpt-5.4-mini"]
    )
    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    # RED: the endpoint prompt is asked exactly ONCE today (the retry short-circuits on
    # the stale env). The fix must ask it a SECOND time so the correction is collected.
    assert len(endpoint_prompts) == 2, (
        f"expected the Azure endpoint to be re-prompted on retry, got "
        f"{len(endpoint_prompts)} endpoint prompt(s)"
    )
    # RED: the corrected endpoint must reach the second validation call. Today the
    # second probe reuses the stale wrong endpoint from os.environ.
    assert endpoint_env_at_validation == [
        "https://wrong-resource.openai.azure.com",
        "https://right-resource.openai.azure.com",
    ]
    assert validator_calls == [("azure-openai", "az-key"), ("azure-openai", "az-key")]
    assert saved_llm_keys == [("azure-openai", "az-key")]
    assert exit_code == 0


def test_run_wizard_azure_endpoint_reprompted_on_repick_then_reselect(
    monkeypatch, tmp_path
) -> None:
    """#3591 FIX 1 (RED): after an Azure validation FAILURE, choosing "Pick a different
    LLM provider" (repick) and then re-selecting Azure must re-prompt the endpoint.

    Today nothing pops AZURE_OPENAI_BASE_URL between wizard iterations
    (``sync_provider_env`` runs only after the loop breaks), so on the re-selection
    ``azure_openai_endpoint_configured()`` is still True and the endpoint prompt is
    short-circuited to the stale bad URL. The fix must re-prompt the endpoint on the
    repick arm so a corrected URL reaches the second validation call.
    """
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "wiped")
    monkeypatch.delenv("AZURE_OPENAI_BASE_URL")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "wiped")
    monkeypatch.delenv("AZURE_OPENAI_API_VERSION")

    endpoint_prompts: list[str] = []
    endpoint_env_at_validation: list[str | None] = []
    validator_calls: list[tuple[str, str]] = []
    saved_llm_keys: list[tuple[str, str]] = []

    provider_responses = iter(["azure-openai", "azure-openai"])
    endpoint_values = iter(
        [
            "https://wrong-resource.openai.azure.com",
            "https://right-resource.openai.azure.com",
        ]
    )

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "What next?" in prompt:
            # First (and only) validation failure -> bail to the provider picker.
            m.ask.return_value = "repick"
        elif "Choose your LLM provider" in prompt:
            m.ask.return_value = next(provider_responses)
        elif "deployment" in prompt.lower() or "model" in prompt:
            m.ask.return_value = "gpt-5.4-mini"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "az-key"
        return m

    def _mock_text(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        endpoint_prompts.append(prompt)
        m = MagicMock()
        m.ask.return_value = next(endpoint_values)
        return m

    def _validate(*, provider, api_key, model):
        validator_calls.append((provider.value, api_key))
        endpoint_env_at_validation.append(os.environ.get("AZURE_OPENAI_BASE_URL"))
        if len(validator_calls) == 1:
            return ValidationResult(ok=False, detail="Azure OpenAI rejected the request.")
        return ValidationResult(ok=True, detail="Azure OpenAI API key validated.")

    monkeypatch.setattr(
        azure_openai, "discover_azure_openai_deployments_from_env", lambda: ["gpt-5.4-mini"]
    )
    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    exit_code = flow.run_wizard()

    # RED: today the endpoint is prompted ONCE; the re-selected Azure short-circuits on
    # the stale AZURE_OPENAI_BASE_URL. The fix must re-prompt it after repick+reselect.
    assert len(endpoint_prompts) == 2, (
        f"expected the Azure endpoint to be re-prompted after repick+reselect, got "
        f"{len(endpoint_prompts)} endpoint prompt(s)"
    )
    # RED: the corrected endpoint must reach the second validation call.
    assert endpoint_env_at_validation == [
        "https://wrong-resource.openai.azure.com",
        "https://right-resource.openai.azure.com",
    ]
    assert exit_code == 0


def test_credential_line_for_saved_summary_ollama_host_unverified() -> None:
    """#3591 FIX 3 (RED): an Ollama host saved via "Save anyway" (unverified) is written
    to .env, not the keyring — the summary must name .env, never the system keychain.

    ``credential_kind == "host"`` values go through the .env branch of
    ``_persist_llm_credential`` (``sync_env_values`` + ``os.environ``), never the
    keyring. The honest summary line must reflect that. Today the function falls into
    the ``credential_kind != "cli"`` block and returns "system keychain (unverified)",
    which lies about where the credential landed.
    """
    ollama = flow.PROVIDER_BY_VALUE["ollama"]
    line = llm_credential._credential_line_for_saved_summary(ollama, credential_state="unverified")
    assert "keychain" not in line.lower(), (
        f"an Ollama host lives in .env, not the keychain; got {line!r}"
    )
    assert ".env" in line, f"the summary must name .env for a host credential; got {line!r}"
    # The line must still disclose that the saved host was not verified.
    assert "unverified" in line.lower(), f"the unverified state must be disclosed; got {line!r}"


def test_credential_line_for_saved_summary_ollama_host_verified() -> None:
    """#3591 FIX 3 (RED): a verified Ollama host is still a .env credential, so the
    summary must name .env and must not claim the system keychain.
    """
    ollama = flow.PROVIDER_BY_VALUE["ollama"]
    line = llm_credential._credential_line_for_saved_summary(ollama)
    assert "keychain" not in line.lower(), (
        f"an Ollama host lives in .env, not the keychain; got {line!r}"
    )
    assert ".env" in line, f"the summary must name .env for a host credential; got {line!r}"
    # A verified host must not be labelled unverified.
    assert "unverified" not in line.lower(), (
        f"a verified host must not read unverified; got {line!r}"
    )


def test_run_wizard_defaults_to_focused_openai_when_provider_is_missing(monkeypatch) -> None:
    """A missing saved provider defaults to the focused OpenAI setup choice."""
    # Arrange
    monkeypatch.setattr(
        flow,
        "local_defaults",
        lambda: {"provider": None, "model": "", "wizard_mode": "", "auth_method": None},
    )
    seen: dict[str, object] = {}

    def _capture_choose(_prompt, _choices, default=None, **_kwargs):
        seen["default"] = default
        raise KeyboardInterrupt  # stop the wizard once the default is known

    monkeypatch.setattr(flow, "choose", _capture_choose)
    monkeypatch.setattr(flow, "render_header", lambda: None)
    monkeypatch.setattr(flow, "step_header", lambda *_a, **_k: None)

    # Act
    with pytest.raises(KeyboardInterrupt):
        flow.run_wizard()

    # Assert
    assert seen["default"] == "openai"


def test_run_wizard_blank_llm_key_defers_setup_instead_of_ending(monkeypatch, tmp_path) -> None:
    """A blank key finishes onboarding; it must not cancel or loop forever.

    The credential prompt was the one step with no "later": its outcomes were
    repick, cancel, save-anyway and continue-unsaved. A user without a key to
    hand had to abandon the wizard.
    """
    # Arrange
    saved_llm_keys: list[tuple[str, str]] = []
    validator_calls: list[tuple[str, str]] = []
    password_asks = 0

    def _mock_select(*_args, **_kwargs):
        prompt = str(_args[0]) if _args else ""
        m = MagicMock()
        if "Choose your LLM provider" in prompt:
            m.ask.return_value = "openai"
        elif "auth method" in prompt:
            m.ask.return_value = "api_key"
        elif "model" in prompt:
            m.ask.return_value = "gpt-5.4-mini"
        elif "integration" in prompt.lower():
            m.ask.return_value = "skip"
        else:
            m.ask.return_value = "quickstart"
        return m

    def _mock_password(*_args, **_kwargs):
        nonlocal password_asks
        password_asks += 1
        m = MagicMock()
        m.ask.return_value = ""  # the user has no key to hand
        return m

    def _validate(*, provider, api_key, model):
        validator_calls.append((provider.value, api_key))
        return ValidationResult(ok=True, detail="unexpected")

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(llm_credential, "validate_provider_credentials", _validate)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    # Act
    exit_code = flow.run_wizard()

    # Assert
    assert exit_code == 0, "a deferred key must not fail onboarding"
    assert saved_llm_keys == [], "nothing may be persisted for a blank key"
    assert validator_calls == [], "a blank key must not be sent to the provider"
    assert password_asks == 1, "the prompt must accept the blank answer, not re-ask"


def test_run_wizard_blank_key_on_kept_provider_reports_deferred_not_keychain(
    monkeypatch, tmp_path, capsys
) -> None:
    """Keeping the saved provider and submitting a blank key defers honestly.

    The summary must not claim the key is in the system keychain when the
    keep-existing-provider branch received DEFERRED from the credential prompt.
    """
    # Arrange: openai already configured but with no stored key; user keeps it.
    saved_llm_keys: list[tuple[str, str]] = []

    def _mock_select(*_args, choices=None, default=None, **_kwargs):
        m = MagicMock()
        prompt = str(_args[0]) if _args else ""
        m.ask.return_value = "skip" if "integration" in prompt.lower() else default
        return m

    def _mock_confirm(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = False  # "Change provider?" -> keep saved openai
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = ""  # the user has no key to hand
        return m

    def _load_saved_openai(_path):
        return {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "openai",
                    "model": "gpt-5.4-mini",
                    "api_key_env": "OPENAI_API_KEY",
                }
            },
        }

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(components, "load_local_config", _load_saved_openai)
    monkeypatch.setattr(components, "has_llm_api_key", lambda _env: False)
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        components,
        "save_api_key",
        lambda provider, value, **_kwargs: _stub_save_recording(saved_llm_keys, provider, value),
    )

    # Act
    exit_code = flow.run_wizard()

    # Assert: onboarding finishes, nothing persists, and the summary is honest.
    output = capsys.readouterr().out
    assert exit_code == 0
    assert saved_llm_keys == []
    assert "not set yet" in output
    assert "system keychain" not in output
