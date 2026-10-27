"""Onboarding uses shared setup while preserving provider recovery outcomes."""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from config import setup_store as wizard_store
from config.secrets.store import SecretSaveResult
from surfaces.cli.wizard import components, flow, llm_credential
from surfaces.cli.wizard.probes import ProbeResult
from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE
from surfaces.shared.llm_setup.selection import select_provider
from surfaces.shared.llm_setup.validation import ValidationResult
from tests.integrations.llm_cli.testing_helpers import write_fake_runnable_cli_bin

_KEYRING_SAVE = SecretSaveResult("fallback", "stored in the local credentials file.")


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


@pytest.fixture
def setup(monkeypatch, tmp_path):
    """Real isolated persistence and fake terminal input; no provider traffic."""
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.setenv("OPENSRE_PROJECT_ENV_PATH", str(tmp_path / ".env"))
    monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr("surfaces.shared.llm_setup.env_sync.PROJECT_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(flow, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "_seed_onboarding_loops", lambda: 0)
    monkeypatch.setattr(
        flow, "probe_local_target", lambda _: ProbeResult("local", True, "synthetic probe")
    )
    monkeypatch.setattr(
        flow,
        "local_defaults",
        lambda: {"provider": None, "model": "", "legacy_api_key": "", "has_api_key": False},
    )
    monkeypatch.setattr(flow, "_choose_onboarding_provider", lambda _: PROVIDER_BY_VALUE["openai"])
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("OPENSRE_LLM_CONNECTION", "local")
    monkeypatch.setattr(llm_credential, "prompt_value", lambda *_a, **_k: "synthetic-key")
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **_: ValidationResult(True, "Synthetic validation"),
    )
    return tmp_path


def test_connect_then_one_picker_commits_matching_store_and_env(monkeypatch, setup):
    calls = []

    def picker(_console, **kwargs):
        assert os.environ["LLM_PROVIDER"] == "anthropic"
        assert kwargs["provider_id"] == "openai"
        calls.append("picker")
        return select_provider("openai", "gpt-test")

    monkeypatch.setattr(flow, "model_picker", picker)
    assert flow.run_wizard() == 0
    assert calls == ["picker"]
    saved = wizard_store.load_local_config(setup / "opensre.json")["targets"]["local"]
    assert saved["provider"] == "openai" and saved["model"] == "gpt-test"
    assert "OPENAI_REASONING_MODEL=gpt-test" in (setup / ".env").read_text()
    assert os.environ["OPENAI_API_KEY"] == "synthetic-key"


@pytest.mark.parametrize("cancel", [False, True])
def test_deferred_or_choose_later_keeps_active_route(monkeypatch, setup, cancel, capsys):
    monkeypatch.setattr(
        llm_credential, "prompt_value", lambda *_a, **_k: "synthetic-key" if cancel else ""
    )
    picker_calls = []
    monkeypatch.setattr(flow, "model_picker", lambda *_a, **_k: picker_calls.append(True) or None)
    assert flow.run_wizard() == 0
    assert os.environ["LLM_PROVIDER"] == "anthropic"
    assert not (setup / "opensre.json").exists()
    assert picker_calls == ([True] if cancel else [])
    output = capsys.readouterr().out
    assert "active model unchanged" in output
    assert "opensre auth login openai" in output


@pytest.mark.parametrize(
    "recovery,state", [("save_anyway", "unverified"), ("continue_unsaved", "unsaved")]
)
def test_recovery_states_and_session_secret_remain_accurate(
    monkeypatch, setup, recovery, state, capsys
):
    if recovery == "save_anyway":
        monkeypatch.setattr(
            llm_credential,
            "validate_provider_credentials",
            lambda **_: ValidationResult(False, "Synthetic offline"),
        )
    else:

        def fail_save(*_a, **_k):
            raise RuntimeError("Synthetic read-only credential store")

        monkeypatch.setattr(components, "save_api_key", fail_save)
    monkeypatch.setattr(llm_credential, "choose", lambda *_a, **_k: recovery)
    monkeypatch.setattr(
        flow, "model_picker", lambda *_a, **_k: select_provider("openai", "gpt-test")
    )
    summary = []
    monkeypatch.setattr(flow, "render_saved_summary", lambda **kwargs: summary.append(kwargs))
    assert flow.run_wizard() == 0
    assert state in summary[0]["credential_line"] or (
        state == "unsaved" and "not saved" in summary[0]["credential_line"]
    )
    assert os.environ["OPENAI_API_KEY"] == "synthetic-key"
    if recovery == "continue_unsaved":
        assert "synthetic-key" not in (setup / ".env").read_text()
    assert "synthetic-key" not in capsys.readouterr().out


def test_retry_then_success_validates_before_picker(monkeypatch, setup):
    events = []
    keys = iter(["synthetic-bad", "synthetic-good"])
    monkeypatch.setattr(llm_credential, "prompt_value", lambda *_a, **_k: next(keys))

    def validate(**kwargs):
        events.append(kwargs["api_key"])
        return ValidationResult(kwargs["api_key"] == "synthetic-good", "Synthetic validation")

    monkeypatch.setattr(llm_credential, "validate_provider_credentials", validate)
    monkeypatch.setattr(llm_credential, "choose", lambda *_a, **_k: "retry")
    monkeypatch.setattr(
        flow,
        "model_picker",
        lambda *_a, **_k: events.append("picker") or select_provider("openai", "gpt-test"),
    )
    assert flow.run_wizard() == 0
    assert events == ["synthetic-bad", "synthetic-good", "picker"]


def test_repick_after_failed_auth_does_not_activate_previous_provider(monkeypatch, setup):
    providers = iter([PROVIDER_BY_VALUE["openai"], PROVIDER_BY_VALUE["deepseek"]])
    monkeypatch.setattr(flow, "_choose_onboarding_provider", lambda _: next(providers))
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **kwargs: ValidationResult(
            kwargs["provider"].value == "deepseek", "Synthetic validation"
        ),
    )
    monkeypatch.setattr(llm_credential, "choose", lambda *_a, **_k: "repick")

    def picker(_console, **kwargs):
        assert kwargs["provider_id"] == "deepseek"
        assert os.environ["LLM_PROVIDER"] == "anthropic"
        return select_provider("deepseek", "deepseek-chat")

    monkeypatch.setattr(flow, "model_picker", picker)
    assert flow.run_wizard() == 0
    assert os.environ["LLM_PROVIDER"] == "deepseek"


def test_legacy_migration_preserves_unsaved_recovery(monkeypatch, setup):
    monkeypatch.setattr(
        flow,
        "local_defaults",
        lambda: {
            "provider": "openai",
            "model": "gpt-test",
            "has_api_key": False,
            "legacy_api_key": "synthetic-legacy",
        },
    )
    monkeypatch.setattr(flow, "confirm", lambda *_a, **_k: False)

    def fail_save(*_a, **_k):
        raise RuntimeError("Synthetic read-only store")

    monkeypatch.setattr(components, "save_api_key", fail_save)
    monkeypatch.setattr(llm_credential, "choose", lambda *_a, **_k: "continue_unsaved")
    monkeypatch.setattr(
        flow, "model_picker", lambda *_a, **_k: select_provider("openai", "gpt-test")
    )
    assert flow.run_wizard() == 0
    assert os.environ["OPENAI_API_KEY"] == "synthetic-legacy"
    assert "synthetic-legacy" not in (setup / ".env").read_text()


def test_local_validation_uses_an_installed_model_then_shared_picker(monkeypatch, setup):
    monkeypatch.setattr(flow, "_choose_onboarding_provider", lambda _: PROVIDER_BY_VALUE["ollama"])
    monkeypatch.setattr(llm_credential, "prompt_value", lambda *_a, **_k: "http://localhost:11434")
    monkeypatch.setattr(
        "surfaces.shared.llm_setup.ollama.list_available_models", lambda _: ("qwen2.5:7b",)
    )
    monkeypatch.setattr(
        "surfaces.shared.llm_setup.ollama_models.list_available_models", lambda _: ("qwen2.5:7b",)
    )
    probed = []
    monkeypatch.setattr(
        llm_credential,
        "validate_provider_credentials",
        lambda **kwargs: (
            probed.append(kwargs["model"]) or ValidationResult(True, "Synthetic local validation")
        ),
    )
    monkeypatch.setattr(
        flow, "model_picker", lambda *_a, **_k: select_provider("ollama", "qwen2.5:7b")
    )
    assert flow.run_wizard() == 0
    assert probed == ["qwen2.5:7b"]
    assert os.environ["OLLAMA_MODEL"] == "qwen2.5:7b"


def test_azure_endpoint_key_and_catalog_precede_validation(monkeypatch, setup):
    provider = PROVIDER_BY_VALUE["azure-openai"]
    monkeypatch.setattr(flow, "_choose_onboarding_provider", lambda _: provider)
    monkeypatch.setattr(
        llm_credential,
        "ensure_provider_endpoint_settings",
        lambda _: {
            "AZURE_OPENAI_BASE_URL": "https://example.invalid",
            "AZURE_OPENAI_API_VERSION": "2024-10-21",
        },
    )
    monkeypatch.setattr(
        flow,
        "ensure_provider_endpoint_settings",
        lambda _: {"AZURE_OPENAI_BASE_URL": "https://example.invalid"},
    )
    monkeypatch.setattr(
        "surfaces.shared.llm_setup.azure_models.discover_azure_openai_deployments_from_env",
        lambda: ("deployment-id",),
    )
    probed = []

    def validate(**kwargs):
        assert os.environ["AZURE_OPENAI_API_KEY"] == "synthetic-key"
        assert os.environ["AZURE_OPENAI_BASE_URL"] == "https://example.invalid"
        probed.append(kwargs["model"])
        return ValidationResult(True, "Synthetic Azure validation")

    monkeypatch.setattr(llm_credential, "validate_provider_credentials", validate)
    monkeypatch.setattr(
        flow, "model_picker", lambda *_a, **_k: select_provider("azure-openai", "deployment-id")
    )
    assert flow.run_wizard() == 0
    assert probed == ["deployment-id"]
    assert os.environ["OPENSRE_LLM_TRANSPORT"] == "litellm"
