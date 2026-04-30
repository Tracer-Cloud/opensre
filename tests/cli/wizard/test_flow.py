from __future__ import annotations

import json
from unittest.mock import MagicMock

from app.cli.wizard import flow
from app.cli.wizard import store as wizard_store
from app.cli.wizard.env_sync import sync_provider_env
from app.cli.wizard.probes import ProbeResult


def test_run_wizard_advanced_remote_falls_back_to_local(monkeypatch, tmp_path, capsys) -> None:
    # advanced -> falls back to local -> change provider? Yes -> pick anthropic -> skip integrations
    select_responses = iter(["advanced", "remote", "anthropic", "skip"])
    confirm_responses = iter([True, True])  # "use local instead?" and "Change provider?"

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
        m.ask.return_value = "secret-key"
        return m

    saved: dict[str, object] = {}
    saved_llm_keys: list[tuple[str, str]] = []

    monkeypatch.setattr(flow, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow, "probe_remote_target", lambda: ProbeResult("remote", True, "remote ok")
    )
    monkeypatch.setattr(
        flow,
        "build_demo_action_response",
        lambda: {
            "success": True,
            "topics": ["recovery_remediation"],
            "guidance": [{"topic": "recovery_remediation"}],
        },
    )

    def _save_local_config(**kwargs):
        saved.update(kwargs)
        return tmp_path / "opensre.json"

    monkeypatch.setattr(flow, "save_local_config", _save_local_config)
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        flow,
        "save_llm_api_key",
        lambda env_var, value: saved_llm_keys.append((env_var, value)),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved["wizard_mode"] == "advanced"
    assert saved["provider"] == "anthropic"
    assert "api_key" not in saved
    assert saved_llm_keys == [("ANTHROPIC_API_KEY", "secret-key")]

    output = capsys.readouterr().out
    assert "summary" in output
    assert "Done." in output


def test_run_wizard_no_saved_provider_shows_selection(monkeypatch, tmp_path) -> None:
    """With no saved config the provider list is shown immediately (no confirm prompt)."""
    select_responses = iter(["quickstart", "anthropic", "skip"])

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "secret-key"
        return m

    monkeypatch.setattr(flow, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(flow, "save_llm_api_key", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        flow,
        "build_demo_action_response",
        lambda: {"success": True, "topics": [], "guidance": []},
    )

    exit_code = flow.run_wizard()
    assert exit_code == 0


def test_run_wizard_configures_optional_integrations(monkeypatch, tmp_path, capsys) -> None:
    select_responses = iter(["quickstart", "anthropic", "grafana"])
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    password_responses = iter(
        [
            "llm-secret",
            "grafana-token",
        ]
    )
    text_responses = iter(["https://grafana.example.com"])

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    monkeypatch.setattr(flow, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(flow, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow,
        "validate_grafana_integration",
        lambda **_kwargs: flow.IntegrationHealthResult(ok=True, detail="Grafana ok"),
    )
    monkeypatch.setattr(
        flow,
        "validate_slack_webhook",
        lambda **_kwargs: flow.IntegrationHealthResult(ok=True, detail="Slack ok"),
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(flow, "save_llm_api_key", lambda *_args, **_kwargs: None)

    def _sync_env_values(values: dict[str, str], **_kwargs):
        synced_env_values.append(values)
        return tmp_path / ".env"

    monkeypatch.setattr(flow, "sync_env_values", _sync_env_values)
    monkeypatch.setattr(
        flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )
    monkeypatch.setattr(
        flow,
        "build_demo_action_response",
        lambda: {"success": True, "topics": ["recovery_remediation"], "guidance": []},
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved_integrations == [
        (
            "grafana",
            {
                "credentials": {
                    "endpoint": "https://grafana.example.com",
                    "api_key": "grafana-token",
                }
            },
        )
    ]
    assert synced_env_values == [
        {
            "GRAFANA_INSTANCE_URL": "https://grafana.example.com",
        },
    ]
    output = capsys.readouterr().out
    assert "Grafana" in output


def test_run_wizard_configures_honeycomb(monkeypatch, tmp_path) -> None:
    select_responses = iter(["quickstart", "anthropic", "honeycomb"])
    password_responses = iter(["llm-secret", "hny_test"])
    text_responses = iter(["prod-api", "https://api.honeycomb.io"])
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    monkeypatch.setattr(flow, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(flow, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow,
        "validate_honeycomb_integration",
        lambda **_kwargs: flow.IntegrationHealthResult(ok=True, detail="Honeycomb ok"),
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(flow, "save_llm_api_key", lambda *_args, **_kwargs: None)

    def _sync_env_values(values: dict[str, str], **_kwargs):
        synced_env_values.append(values)
        return tmp_path / ".env"

    monkeypatch.setattr(flow, "sync_env_values", _sync_env_values)
    monkeypatch.setattr(
        flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )
    monkeypatch.setattr(
        flow,
        "build_demo_action_response",
        lambda: {"success": True, "topics": [], "guidance": []},
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved_integrations == [
        (
            "honeycomb",
            {
                "credentials": {
                    "api_key": "hny_test",
                    "dataset": "prod-api",
                    "base_url": "https://api.honeycomb.io",
                }
            },
        )
    ]
    assert synced_env_values == [
        {
            "HONEYCOMB_DATASET": "prod-api",
            "HONEYCOMB_API_URL": "https://api.honeycomb.io",
        }
    ]


def test_run_wizard_configures_coralogix(monkeypatch, tmp_path) -> None:
    select_responses = iter(["quickstart", "anthropic", "coralogix"])
    password_responses = iter(["llm-secret", "cx_test"])
    text_responses = iter(
        [
            "https://api.coralogix.com",
            "payments",
            "worker",
        ]
    )
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    monkeypatch.setattr(flow, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(flow, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow,
        "validate_coralogix_integration",
        lambda **_kwargs: flow.IntegrationHealthResult(ok=True, detail="Coralogix ok"),
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(flow, "save_llm_api_key", lambda *_args, **_kwargs: None)

    def _sync_env_values(values: dict[str, str], **_kwargs):
        synced_env_values.append(values)
        return tmp_path / ".env"

    monkeypatch.setattr(flow, "sync_env_values", _sync_env_values)
    monkeypatch.setattr(
        flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )
    monkeypatch.setattr(
        flow,
        "build_demo_action_response",
        lambda: {"success": True, "topics": [], "guidance": []},
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved_integrations == [
        (
            "coralogix",
            {
                "credentials": {
                    "api_key": "cx_test",
                    "base_url": "https://api.coralogix.com",
                    "application_name": "payments",
                    "subsystem_name": "worker",
                }
            },
        )
    ]
    assert synced_env_values == [
        {
            "CORALOGIX_API_URL": "https://api.coralogix.com",
            "CORALOGIX_APPLICATION_NAME": "payments",
            "CORALOGIX_SUBSYSTEM_NAME": "worker",
        }
    ]


def test_run_wizard_configures_github_mcp_and_sentry(monkeypatch, tmp_path, capsys) -> None:
    select_responses = iter(
        [
            "quickstart",
            "anthropic",
            "github",
            flow.DEFAULT_GITHUB_MCP_MODE,
            "auto",
            "any",
            "summary",
        ]
    )
    text_responses = iter(
        [
            flow.DEFAULT_GITHUB_MCP_URL,
            "repos,issues,pull_requests,actions,search",
        ]
    )
    password_responses = iter(
        [
            "llm-secret",
            "ghp_test",
        ]
    )
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    monkeypatch.setattr(flow, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(flow, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow,
        "validate_github_mcp_integration",
        lambda **_kwargs: flow.IntegrationHealthResult(ok=True, detail="GitHub MCP ok"),
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(flow, "save_llm_api_key", lambda *_args, **_kwargs: None)

    def _sync_env_values(values: dict[str, str], **_kwargs):
        synced_env_values.append(values)
        return tmp_path / ".env"

    monkeypatch.setattr(
        flow,
        "sync_env_values",
        _sync_env_values,
    )
    monkeypatch.setattr(
        flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )
    monkeypatch.setattr(
        flow,
        "build_demo_action_response",
        lambda: {"success": True, "topics": ["recovery_remediation"], "guidance": []},
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved_integrations == [
        (
            "github",
            {
                "credentials": {
                    "url": flow.DEFAULT_GITHUB_MCP_URL,
                    "mode": flow.DEFAULT_GITHUB_MCP_MODE,
                    "auth_token": "ghp_test",
                    "command": "",
                    "args": [],
                    "toolsets": ["repos", "issues", "pull_requests", "actions", "search"],
                }
            },
        ),
    ]
    assert synced_env_values == [
        {
            "GITHUB_MCP_URL": flow.DEFAULT_GITHUB_MCP_URL,
            "GITHUB_MCP_MODE": flow.DEFAULT_GITHUB_MCP_MODE,
            "GITHUB_MCP_COMMAND": "",
            "GITHUB_MCP_ARGS": "",
            "GITHUB_MCP_TOOLSETS": "repos,issues,pull_requests,actions,search",
        },
    ]

    output = capsys.readouterr().out
    assert "GitHub MCP" in output


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

    monkeypatch.setattr(flow, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        flow,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "openai",
                    "model": "gpt-5.4",
                    "api_key_env": "OPENAI_API_KEY",
                    "api_key": "saved-secret",
                }
            },
        },
    )
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "has_llm_api_key", lambda _env: False)
    monkeypatch.setattr(
        flow,
        "build_demo_action_response",
        lambda: {"success": True, "topics": [], "guidance": []},
    )

    def _save_local_config(**kwargs):
        saved.update(kwargs)
        return tmp_path / "opensre.json"

    monkeypatch.setattr(flow, "save_local_config", _save_local_config)
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        flow,
        "save_llm_api_key",
        lambda env_var, value: saved_llm_keys.append((env_var, value)),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved["wizard_mode"] == "quickstart"
    assert saved["provider"] == "openai"
    assert saved["model"] == "gpt-5.4"
    assert "api_key" not in saved
    assert saved_llm_keys == [("OPENAI_API_KEY", "saved-secret")]


def test_run_wizard_persists_matching_local_config_and_env(monkeypatch, tmp_path) -> None:
    select_responses = iter(["quickstart", "openai", "skip"])
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

    monkeypatch.setattr(flow, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow, "get_store_path", lambda: store_path)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow,
        "build_demo_action_response",
        lambda: {"success": True, "topics": [], "guidance": []},
    )
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
        flow,
        "save_llm_api_key",
        lambda env_var, value: saved_llm_keys.append((env_var, value)),
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
    assert saved_llm_keys == [("OPENAI_API_KEY", "openai-secret")]


def test_run_wizard_codex_skips_api_key_and_runs_cli_onboarding(monkeypatch, tmp_path) -> None:
    select_responses = iter(["quickstart", "codex", "skip"])
    saved_llm_keys: list[tuple[str, str]] = []
    cli_onboarding_providers: list[str] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _cli_onboarding(provider):
        cli_onboarding_providers.append(provider.value)
        return "ok"

    store_path = tmp_path / "opensre.json"
    env_path = tmp_path / ".env"

    monkeypatch.setattr(flow, "select_prompt", _mock_select)
    monkeypatch.setattr(flow, "get_store_path", lambda: store_path)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "_run_cli_llm_onboarding", _cli_onboarding)
    monkeypatch.setattr(
        flow,
        "build_demo_action_response",
        lambda: {"success": True, "topics": [], "guidance": []},
    )
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
        flow,
        "save_llm_api_key",
        lambda env_var, value: saved_llm_keys.append((env_var, value)),
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert cli_onboarding_providers == ["codex"]
    assert saved_llm_keys == []

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    env_values = env_path.read_text(encoding="utf-8")
    assert payload["targets"]["local"]["provider"] == "codex"
    assert payload["targets"]["local"]["api_key_env"] == ""
    assert payload["targets"]["local"]["model_env"] == "CODEX_MODEL"
    assert "LLM_PROVIDER=codex\n" in env_values
    assert "CODEX_MODEL=\n" in env_values


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

    monkeypatch.setattr(flow, "_choose", lambda *_args, **_kwargs: "repick")
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

    monkeypatch.setattr(flow, "_choose", lambda *_args, **_kwargs: "retry")
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

    monkeypatch.setattr(flow, "_choose", lambda *_args, **_kwargs: "repick")
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

    monkeypatch.setattr(flow, "_choose", lambda *_args, **_kwargs: "retry")
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

    monkeypatch.setattr(flow, "_choose", lambda *_args, **_kwargs: "repick")
    result = flow._run_cli_llm_onboarding(provider)
    assert result == "repick"


def test_run_cli_llm_onboarding_path_override_then_ok(monkeypatch, tmp_path) -> None:
    # Create a real executable so diagnose_binary_path accepts it.
    fake_bin = tmp_path / "codex"
    fake_bin.write_bytes(b"")
    import os as _os

    _os.chmod(fake_bin, 0o700)

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

    monkeypatch.setattr(flow, "_choose", lambda *_args, **_kwargs: "path")
    monkeypatch.setattr(flow, "_prompt_value", lambda *_args, **_kwargs: str(fake_bin))
    monkeypatch.setattr(flow, "sync_env_values", lambda *_args, **_kwargs: None)

    original_codex_bin = _os.environ.get("CODEX_BIN")
    try:
        result = flow._run_cli_llm_onboarding(provider)

        assert result == "ok"
        assert len(detect_calls) == 2
        # os.environ must be updated in-process so the next detect() call in the
        # retry loop resolves the new binary without a process restart.
        assert _os.environ.get("CODEX_BIN") == str(fake_bin)
    finally:
        if original_codex_bin is None:
            _os.environ.pop("CODEX_BIN", None)
        else:
            _os.environ["CODEX_BIN"] = original_codex_bin


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

    monkeypatch.setattr(flow, "_choose", lambda *_args, **_kwargs: "retry")
    result = flow._run_cli_llm_onboarding(provider)
    assert result == "abort"
    assert adapter.detect.call_count == 10


def test_credential_line_for_saved_summary_cli_codex() -> None:
    from app.cli.wizard import config as wizard_config

    codex = next(p for p in wizard_config.SUPPORTED_PROVIDERS if p.value == "codex")
    assert flow._credential_line_for_saved_summary(codex) == ("OpenAI Codex CLI (Run: codex login)")


def test_credential_line_for_saved_summary_anthropic() -> None:
    from app.cli.wizard import config as wizard_config

    anthropic = next(p for p in wizard_config.SUPPORTED_PROVIDERS if p.value == "anthropic")
    assert flow._credential_line_for_saved_summary(anthropic) == "system keychain"


def test_credential_line_for_saved_summary_cli_without_factory() -> None:
    from app.cli.wizard.config import ModelOption, ProviderOption

    p = ProviderOption(
        value="fakecli",
        label="Fake CLI",
        group="Local CLI providers",
        api_key_env="",
        model_env="FAKE_MODEL",
        default_model="",
        models=(ModelOption(value="", label="default"),),
        credential_kind="cli",
        adapter_factory=None,
    )
    assert flow._credential_line_for_saved_summary(p) == "Fake CLI (CLI)"


def test_run_wizard_configures_gitlab(monkeypatch, tmp_path) -> None:
    select_responses = iter(["quickstart", "anthropic", "gitlab"])
    password_responses = iter(["llm-secret", "glpat_test"])
    text_responses = iter(["https://gitlab.example.com/api/v4"])
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    monkeypatch.setattr(flow, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(flow, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow,
        "validate_gitlab_integration",
        lambda **_kwargs: flow.IntegrationHealthResult(ok=True, detail="GitLab ok"),
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(flow, "save_llm_api_key", lambda *_args, **_kwargs: None)

    def _sync_env_values(values: dict[str, str], **_kwargs):
        synced_env_values.append(values)
        return tmp_path / ".env"

    monkeypatch.setattr(flow, "sync_env_values", _sync_env_values)
    monkeypatch.setattr(
        flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )
    monkeypatch.setattr(
        flow,
        "build_demo_action_response",
        lambda: {"success": True, "topics": [], "guidance": []},
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved_integrations == [
        (
            "gitlab",
            {
                "credentials": {
                    "base_url": "https://gitlab.example.com/api/v4",
                    "auth_token": "glpat_test",
                }
            },
        )
    ]
    assert synced_env_values == [
        {
            "GITLAB_BASE_URL": "https://gitlab.example.com/api/v4",
            "GITLAB_ACCESS_TOKEN": "glpat_test",
        }
    ]


def test_run_wizard_gitlab_retries_on_validation_failure(monkeypatch, tmp_path) -> None:
    """When GitLab validation fails the first time, the wizard retries and succeeds."""
    select_responses = iter(["quickstart", "anthropic", "gitlab"])
    # First attempt: wrong token; second attempt: correct token
    password_responses = iter(["llm-secret", "bad_token", "glpat_good"])
    # base_url is prompted on each retry
    text_responses = iter(
        [
            "https://gitlab.com/api/v4",
            "https://gitlab.com/api/v4",
        ]
    )
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []
    validation_call_count = 0

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_responses)
        return m

    def _mock_text(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_responses)
        return m

    def _validate_gitlab(**_kwargs):
        nonlocal validation_call_count
        validation_call_count += 1
        if validation_call_count == 1:
            return flow.IntegrationHealthResult(ok=False, detail="Unauthorized")
        return flow.IntegrationHealthResult(ok=True, detail="GitLab ok")

    monkeypatch.setattr(flow, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(flow, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "validate_gitlab_integration", _validate_gitlab)
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(flow, "save_llm_api_key", lambda *_args, **_kwargs: None)

    def _sync_env_values(values: dict[str, str], **_kwargs):
        synced_env_values.append(values)
        return tmp_path / ".env"

    monkeypatch.setattr(flow, "sync_env_values", _sync_env_values)
    monkeypatch.setattr(
        flow,
        "upsert_integration",
        lambda service, payload: saved_integrations.append((service, payload)),
    )
    monkeypatch.setattr(
        flow,
        "build_demo_action_response",
        lambda: {"success": True, "topics": [], "guidance": []},
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert validation_call_count == 2
    assert saved_integrations == [
        (
            "gitlab",
            {
                "credentials": {
                    "base_url": "https://gitlab.com/api/v4",
                    "auth_token": "glpat_good",
                }
            },
        )
    ]
    assert synced_env_values == [
        {
            "GITLAB_BASE_URL": "https://gitlab.com/api/v4",
            "GITLAB_ACCESS_TOKEN": "glpat_good",
        }
    ]


def test_run_wizard_switches_provider_and_keeps_store_and_env_in_sync(
    monkeypatch, tmp_path
) -> None:
    # Saved: anthropic. User says yes to "Change provider?" and picks openai.
    select_responses = iter(["quickstart", "openai", "skip"])
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

    monkeypatch.setattr(flow, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow, "get_store_path", lambda: store_path)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow,
        "build_demo_action_response",
        lambda: {"success": True, "topics": [], "guidance": []},
    )
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
        flow,
        "save_llm_api_key",
        lambda env_var, value: saved_llm_keys.append((env_var, value)),
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
    assert "ANTHROPIC_API_KEY=" not in env_values
    assert "OPENAI_REASONING_MODEL=" in env_values
    assert saved_llm_keys == [("OPENAI_API_KEY", "fresh-openai-key")]
