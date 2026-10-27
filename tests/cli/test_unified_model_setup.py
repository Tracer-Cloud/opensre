"""Connection and selection contracts shared by CLI and REPL."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from surfaces.cli.app import cli


@pytest.fixture
def selection_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    monkeypatch.setenv("OPENSRE_PROJECT_ENV_PATH", str(path))
    monkeypatch.setenv("OPENSRE_ACCOUNT_METADATA_PATH", str(tmp_path / "account.json"))
    monkeypatch.setenv("OPENSRE_LLM_CONNECTION", "local")
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "auth.json"))
    monkeypatch.setattr("surfaces.shared.llm_setup.env_sync.PROJECT_ENV_PATH", path)
    monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", path)
    monkeypatch.setattr("config.setup_store.get_store_path", lambda: tmp_path / "setup.json")
    return path


def test_switch_preserves_other_connections_and_rejects_invalid_selection(
    monkeypatch: pytest.MonkeyPatch, selection_paths: Path
) -> None:
    from surfaces.shared.llm_setup.selection import SelectionError, select_provider

    selection_paths.write_text(
        "CUSTOM_OPENAI_BASE_URL=https://example.test/v1\nCUSTOM_OPENAI_REASONING_MODEL=private-model\n"
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-key")
    result = select_provider("anthropic", "claude-sonnet-4-6")
    assert result.model == "claude-sonnet-4-6"
    saved = selection_paths.read_text()
    assert "CUSTOM_OPENAI_BASE_URL=https://example.test/v1" in saved
    assert "CUSTOM_OPENAI_REASONING_MODEL=private-model" in saved
    with pytest.raises(SelectionError, match="unknown model"):
        select_provider("anthropic", "not-a-model")
    assert selection_paths.read_text() == saved


def test_noninteractive_login_connects_without_switching(
    monkeypatch: pytest.MonkeyPatch, selection_paths: Path
) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    result = CliRunner().invoke(
        cli,
        [
            "auth",
            "login",
            "deepseek",
            "--api-key",
            "synthetic-key",
            "--no-validate",
            "--no-open-browser",
        ],
    )
    assert result.exit_code == 0, result.output
    assert os.environ["LLM_PROVIDER"] == "anthropic"
    assert (
        not selection_paths.exists() or "LLM_PROVIDER=deepseek" not in selection_paths.read_text()
    )
    assert "opensre model" in result.output


def test_model_cli_and_repl_share_selection(
    monkeypatch: pytest.MonkeyPatch, selection_paths: Path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    result = CliRunner().invoke(cli, ["model", "set", "openai", "gpt-5.4-mini"])
    assert result.exit_code == 0, result.output
    assert "LLM_PROVIDER=openai" in selection_paths.read_text()
    assert "OPENAI_REASONING_MODEL=gpt-5.4-mini" in selection_paths.read_text()


def test_login_handoff_choose_later_keeps_auth_without_selection(
    monkeypatch: pytest.MonkeyPatch, selection_paths: Path
) -> None:
    import surfaces.cli.commands.auth as auth
    from surfaces.shared.llm_setup import model_picker

    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(auth, "repl_tty_interactive", lambda: True)
    asked: list[str | None] = []
    monkeypatch.setattr(
        model_picker, "choose_model", lambda _console, **kw: asked.append(kw.get("provider"))
    )
    result = CliRunner().invoke(
        cli,
        [
            "auth",
            "login",
            "deepseek",
            "--api-key",
            "synthetic-key",
            "--no-validate",
            "--no-open-browser",
        ],
    )
    assert result.exit_code == 0, result.output
    assert asked == ["deepseek"]
    assert os.environ["LLM_PROVIDER"] == "anthropic"
    from config.llm_auth.credentials import status

    assert status("deepseek").configured


def test_catalog_preserves_source_identity_and_custom_models(
    monkeypatch: pytest.MonkeyPatch, selection_paths: Path
) -> None:
    from surfaces.shared.llm_setup.model_choices import provider_choices

    monkeypatch.setenv("OPENAI_REASONING_MODEL", "private-openai-model")
    options = provider_choices("openai")
    assert any(row.model == "private-openai-model" for row in options)
    assert all(row.provider == "openai" for row in options)
    assert all("API" in row.source for row in options)


def test_model_picker_one_enter_selects_without_another_menu(monkeypatch, tmp_path) -> None:
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from rich.console import Console

    from surfaces.shared.llm_setup import model_picker
    from surfaces.shared.llm_setup.auth_results import SelectionResult
    from surfaces.shared.llm_setup.model_choices import ModelChoice

    rows = [
        ModelChoice("claude-code", "sonnet", "Sonnet", "CLI-managed", True),
        ModelChoice("claude-code", "opus", "Opus", "CLI-managed"),
    ]
    monkeypatch.setattr(model_picker, "connected_choices", lambda **_: rows)
    monkeypatch.setattr(model_picker, "run_command_input", lambda app: app.run())
    calls = []

    def select(provider, model):
        calls.append((provider, model))
        return SelectionResult(provider, model, tmp_path / ".env")

    monkeypatch.setattr(model_picker, "select_provider", select)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        pipe.send_text("\x1b[B\r")
        result = model_picker.choose_model(Console())
    assert result.model == "opus"
    assert calls == [("claude-code", "opus")]


def test_cancelled_picker_does_not_select(monkeypatch) -> None:
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from rich.console import Console

    from surfaces.shared.llm_setup import model_picker
    from surfaces.shared.llm_setup.model_choices import ModelChoice

    monkeypatch.setattr(
        model_picker,
        "connected_choices",
        lambda **_: [ModelChoice("claude-code", "sonnet", "Sonnet", "CLI-managed")],
    )
    monkeypatch.setattr(model_picker, "run_command_input", lambda app: app.run())
    monkeypatch.setattr(
        model_picker, "select_provider", lambda *_: pytest.fail("Cancellation selected a model")
    )
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        pipe.send_text("\x03")
        assert model_picker.choose_model(Console(), provider="claude-code") is None


def test_json_show_reports_source_without_credentials(monkeypatch, selection_paths):
    import json

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "saved-custom-model")
    result = CliRunner().invoke(cli, ["--json", "model", "show"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["provider"] == "openai"
    assert payload["model"] == "saved-custom-model"
    assert "API billing" in payload["source"]
    assert "synthetic-secret" not in result.output


def test_connect_from_picker_returns_after_choose_later(monkeypatch, tmp_path):
    from rich.console import Console

    from surfaces.shared.llm_setup import model_picker
    from surfaces.shared.llm_setup.auth_results import SelectionResult
    from surfaces.shared.llm_setup.model_choices import ModelChoice

    calls = []
    inputs = iter(["__connect__", "__later__", "openai:gpt-5.4-mini"])
    row = ModelChoice("openai", "gpt-5.4-mini", "GPT mini", "OpenAI · API billing")
    monkeypatch.setattr(
        model_picker, "connected_choices", lambda **kw: calls.append(kw.get("provider")) or [row]
    )
    monkeypatch.setattr(model_picker, "run_command_input", lambda _: next(inputs))
    monkeypatch.setattr(
        model_picker,
        "select_provider",
        lambda provider, model: SelectionResult(provider, model, tmp_path / ".env"),
    )
    result = model_picker.choose_model(Console(), connect=lambda: "openai")
    assert result.provider == "openai"
    assert calls == [None, "openai", None]


def test_hosted_round_trip_retains_identity_and_requires_explicit_selection(
    monkeypatch, selection_paths
):
    from types import SimpleNamespace

    from surfaces.shared.llm_setup import hosted_connection, model_choices
    from surfaces.shared.llm_setup.selection import select_provider

    resolved = []
    monkeypatch.setattr(
        hosted_connection, "load_account_record", lambda: SimpleNamespace(llm_model="hosted-model")
    )
    monkeypatch.setattr(
        "config.account.resolve_account_token", lambda: resolved.append(True) or "synthetic-token"
    )
    monkeypatch.setenv("OPENSRE_LLM_CONNECTION", "account")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-key")
    monkeypatch.setattr(model_choices, "SUPPORTED_PROVIDERS", ())
    assert model_choices.connected_choices()[0].active
    assert not resolved
    select_provider("openai", "gpt-5.4-mini")
    hosted = model_choices.connected_choices()[0]
    assert hosted.provider == "opensre-hosted" and not hosted.active
    assert not resolved
    select_provider(hosted.provider, hosted.model)
    assert os.environ["OPENSRE_LLM_CONNECTION"] == "account"
    assert os.environ["LLM_PROVIDER"] == "openai"
    assert resolved == [True]


def test_stale_cli_connection_does_not_hide_usable_api_models(monkeypatch, selection_paths):
    from config.llm_auth.records import save_provider_auth_record
    from surfaces.shared.llm_setup import model_choices
    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE

    monkeypatch.setenv("LLM_PROVIDER", "claude-code")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    monkeypatch.setattr(
        model_choices,
        "SUPPORTED_PROVIDERS",
        tuple(PROVIDER_BY_VALUE[p] for p in ("openai", "claude-code")),
    )
    save_provider_auth_record(
        provider="claude-code",
        auth_name="claude-code",
        kind="cli",
        source="cli",
        detail="Reconnect required",
        verified=False,
        stale=True,
    )
    rows = model_choices.connected_choices()
    assert any(row.provider == "openai" for row in rows)
    assert any(row.provider == "claude-code" and "Reconnect" in row.availability for row in rows)


def test_azure_login_uses_discovered_deployments_not_global_model_ids(monkeypatch, selection_paths):
    from surfaces.shared.llm_setup import model_discovery
    from surfaces.shared.llm_setup.model_choices import provider_choices

    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://resource.openai.azure.com")
    monkeypatch.delenv("AZURE_OPENAI_REASONING_MODEL", raising=False)
    monkeypatch.setattr(
        model_discovery,
        "discover_models",
        lambda provider: (
            [("team-deployment", "Team deployment")] if provider.value == "azure-openai" else None
        ),
    )
    result = CliRunner().invoke(
        cli,
        [
            "auth",
            "login",
            "azure-openai",
            "--api-key",
            "synthetic-key",
            "--no-validate",
            "--no-open-browser",
        ],
    )
    assert result.exit_code == 0, result.output
    assert provider_choices("azure-openai")[0].model == "team-deployment"
    assert os.environ["LLM_PROVIDER"] == "openai"


def test_custom_endpoint_login_remembers_candidate_without_activating(monkeypatch, selection_paths):
    from surfaces.shared.llm_setup.model_choices import provider_choices

    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("CUSTOM_OPENAI_BASE_URL", "https://gateway.example.test/v1")
    monkeypatch.delenv("CUSTOM_OPENAI_REASONING_MODEL", raising=False)
    result = CliRunner().invoke(
        cli,
        [
            "auth",
            "login",
            "custom-openai",
            "--api-key",
            "synthetic",
            "--model",
            "gateway-model",
            "--no-validate",
            "--no-open-browser",
        ],
    )
    assert result.exit_code == 0, result.output
    assert provider_choices("custom-openai")[0].model == "gateway-model"
    assert os.environ["LLM_PROVIDER"] == "openai"
    assert not os.getenv("CUSTOM_OPENAI_REASONING_MODEL")


def test_restore_cli_clears_override_and_keeps_gateway_model(monkeypatch, selection_paths):
    from config.llm_auth.records import save_provider_auth_record
    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE
    from surfaces.shared.llm_setup.selection import default_model

    monkeypatch.setenv("CLAUDE_CODE_MODEL", "opus")
    monkeypatch.setenv("CUSTOM_OPENAI_REASONING_MODEL", "gateway-model")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    save_provider_auth_record(
        provider="claude-code",
        auth_name="claude-code",
        kind="cli",
        source="cli",
        detail="Previously connected CLI",
        verified=False,
    )
    result = CliRunner().invoke(cli, ["model", "restore", "claude-code"])
    assert result.exit_code == 0, result.output
    assert os.environ["CLAUDE_CODE_MODEL"] == ""
    assert default_model(PROVIDER_BY_VALUE["custom-openai"]) == "gateway-model"


def test_toolcall_show_uses_effective_tiered_default(monkeypatch, selection_paths):
    import json

    from config.llm_settings import LLMSettings
    from core.llm.provider_models import resolve_provider_models

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic")
    for key in ("ANTHROPIC_MODEL", "ANTHROPIC_REASONING_MODEL", "ANTHROPIC_TOOLCALL_MODEL"):
        monkeypatch.delenv(key, raising=False)
    reasoning, toolcall = resolve_provider_models(LLMSettings.from_env(), "anthropic")
    assert reasoning != toolcall
    result = CliRunner().invoke(cli, ["--json", "model", "toolcall", "show"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"provider": "anthropic", "model": toolcall}


def test_reused_key_still_validates_and_does_not_switch_on_rejection(monkeypatch, selection_paths):
    from rich.console import Console

    from surfaces.shared.llm_setup import connection, validation
    from surfaces.shared.llm_setup.persist import AuthSetupError
    from surfaces.shared.llm_setup.validation_result import ValidationResult

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "expired-synthetic-key")
    calls = []

    def reject(**kwargs):
        calls.append((kwargs["api_key"], kwargs["model"]))
        return ValidationResult(ok=False, detail="API key rejected")

    monkeypatch.setattr(validation, "validate_provider_credentials", reject)
    with pytest.raises(AuthSetupError, match="API key rejected"):
        connection.connect_provider(Console(), "openai", model="gpt-5.4")
    assert calls == [("expired-synthetic-key", "gpt-5.4")]
    assert os.environ["LLM_PROVIDER"] == "anthropic"
    assert not selection_paths.exists()


def test_connecting_ambient_provider_makes_its_models_selectable(monkeypatch, selection_paths):
    from rich.console import Console

    from config.llm_auth import credentials
    from config.llm_auth.credentials import CredentialSource, CredentialStatus
    from surfaces.shared.llm_setup import connection
    from surfaces.shared.llm_setup.model_choices import connected_choices

    monkeypatch.setenv("LLM_PROVIDER", "openai")

    # Stand in for the ambient IAM/ADC probe, not the picker or connect workflow.
    def status(provider):
        return CredentialStatus(provider, True, CredentialSource.AMBIENT, False, False, "IAM ready")

    monkeypatch.setattr(credentials, "status", status)
    monkeypatch.setattr(connection, "status", status)
    connection.connect_provider(Console(), "bedrock")
    rows = connected_choices(provider="bedrock")
    assert rows and all(row.provider == "bedrock" for row in rows)
    assert os.environ["LLM_PROVIDER"] == "openai"


def test_model_inventory_and_selection_are_probe_free(monkeypatch, selection_paths):
    from io import StringIO

    from rich.console import Console

    from config.llm_auth import cli_auth, credentials, records
    from config.llm_auth.cli_auth import CliAuthChecker, CliAuthState
    from surfaces.shared.llm_setup import model_choices
    from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE
    from surfaces.shared.llm_setup.model_picker import render_models
    from surfaces.shared.llm_setup.selection import SelectionError, select_provider

    monkeypatch.setenv("LLM_PROVIDER", "claude-code")
    monkeypatch.setenv("GROK_CLI_MODEL", "grok-build")
    monkeypatch.setattr(
        model_choices,
        "SUPPORTED_PROVIDERS",
        tuple(PROVIDER_BY_VALUE[p] for p in ("claude-code", "grok-cli", "bedrock")),
    )
    records.save_provider_auth_record(
        provider="claude-code",
        auth_name="claude-code",
        kind="cli",
        source="cli",
        detail="Previously connected; auth checked on use",
        verified=False,
    )
    records.save_provider_auth_record(
        provider="bedrock",
        auth_name="bedrock",
        kind="none",
        source="ambient",
        detail="Saved cloud connection",
        verified=False,
    )
    ambient_probes = []

    def ambient_probe(_spec):
        ambient_probes.append("bedrock")
        raise AssertionError("Model browsing must not probe cloud credentials")

    monkeypatch.setitem(credentials._AMBIENT_PROBES, "bedrock", ambient_probe)
    reads = []
    load = records._load_unlocked

    def read(path):
        reads.append(path)
        return load(path)

    monkeypatch.setattr(records, "_load_unlocked", read)
    probes = []

    def probe(provider):
        probes.append(provider)
        return CliAuthState(True, None, "Installed CLI; auth status unknown")

    monkeypatch.setattr(cli_auth, "_installed", CliAuthChecker(probe))
    output = StringIO()
    render_models(Console(file=output, width=160))
    rows = model_choices.connected_choices()
    assert {row.provider for row in rows} == {"claude-code", "bedrock"}
    assert not ambient_probes
    assert not probes, f"Model browsing unexpectedly launched vendor probes: {probes}"
    assert len(reads) == 2, "Each inventory should read provider metadata just once"
    select_provider("claude-code", "sonnet")
    assert not probes, "Selecting a saved model must not start an auth probe"
    assert os.environ["CLAUDE_CODE_MODEL"] == "sonnet"
    with pytest.raises(SelectionError, match="/login"):
        select_provider("grok-cli", "grok-build")
    assert not probes
