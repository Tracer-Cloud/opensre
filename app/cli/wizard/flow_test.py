from __future__ import annotations

import json
from unittest.mock import MagicMock

from app.cli.wizard import flow
from app.cli.wizard import store as wizard_store
from app.cli.wizard.env_sync import sync_provider_env
from app.cli.wizard.probes import ProbeResult
from app.cli.wizard.validation import ValidationResult


def test_run_wizard_advanced_remote_falls_back_to_local(monkeypatch, tmp_path, capsys) -> None:
    select_responses = iter(["advanced", "remote", "anthropic", "claude-opus-4-20250514"])
    confirm_responses = iter([True])

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_confirm(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(confirm_responses)
        return m

    def _mock_checkbox(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = []
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "secret-key"
        return m

    saved: dict[str, object] = {}

    monkeypatch.setattr(flow, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "confirm", _mock_confirm)
    monkeypatch.setattr(flow, "checkbox_prompt", _mock_checkbox)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(flow, "probe_remote_target", lambda: ProbeResult("remote", True, "remote ok"))
    monkeypatch.setattr(
        flow,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="validated", sample_response="OpenSRE ready"),
    )
    monkeypatch.setattr(
        flow,
        "build_demo_action_response",
        lambda: {"success": True, "topics": ["recovery_remediation"], "guidance": [{"topic": "recovery_remediation"}]},
    )

    def _save_local_config(**kwargs):
        saved.update(kwargs)
        return tmp_path / "opensre.json"

    monkeypatch.setattr(flow, "save_local_config", _save_local_config)
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved["wizard_mode"] == "advanced"
    assert saved["provider"] == "anthropic"
    assert saved["model"] == "claude-opus-4-20250514"
    assert saved["api_key"] == "secret-key"

    output = capsys.readouterr().out
    assert "Sample: OpenSRE ready" in output
    assert "summary" in output
    assert "Done." in output


def test_run_wizard_retries_invalid_api_key(monkeypatch, tmp_path, capsys) -> None:
    validations = iter(
        [
            ValidationResult(ok=False, detail="bad key"),
            ValidationResult(ok=True, detail="validated", sample_response="OpenSRE ready"),
        ]
    )

    select_responses = iter(["quickstart", "anthropic", "claude-opus-4-20250514"])

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_checkbox(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = []
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "secret-key"
        return m

    monkeypatch.setattr(flow, "select_prompt", _mock_select)
    monkeypatch.setattr(flow, "checkbox_prompt", _mock_checkbox)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow,
        "validate_provider_credentials",
        lambda **_kwargs: next(validations),
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")
    monkeypatch.setattr(
        flow,
        "build_demo_action_response",
        lambda: {"success": True, "topics": ["recovery_remediation"], "guidance": []},
    )

    exit_code = flow.run_wizard()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Validation failed: bad key" in output
    assert "Enter retries the current key. Paste a new one to replace it." in output
    assert "validated" in output


def test_run_wizard_configures_optional_integrations(monkeypatch, tmp_path, capsys) -> None:
    select_responses = iter(["quickstart", "anthropic", "claude-opus-4-20250514", "role"])
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_checkbox(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = ["grafana", "slack"]
        return m

    password_responses = iter([
        "llm-secret",
        "grafana-token",
        "https://hooks.slack.com/services/T000/B000/abc",
    ])
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
    monkeypatch.setattr(flow, "checkbox_prompt", _mock_checkbox)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(flow, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="validated", sample_response="OpenSRE ready"),
    )
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
            "GRAFANA_READ_TOKEN": "grafana-token",
        },
        {
            "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T000/B000/abc",
        },
    ]

    output = capsys.readouterr().out
    assert "Grafana, Slack" in output


def test_run_wizard_configures_github_mcp_and_sentry(monkeypatch, tmp_path, capsys) -> None:
    select_responses = iter([
        "quickstart",
        "anthropic",
        "claude-opus-4-20250514",
        flow.DEFAULT_GITHUB_MCP_MODE,
    ])
    text_responses = iter([
        flow.DEFAULT_GITHUB_MCP_URL,
        "repos,issues,pull_requests,actions",
        flow.DEFAULT_SENTRY_URL,
        "demo-org",
        "payments",
    ])
    password_responses = iter([
        "llm-secret",
        "ghp_test",
        "sntrys_test",
    ])
    saved_integrations: list[tuple[str, dict]] = []
    synced_env_values: list[dict[str, str]] = []

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_checkbox(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = ["github", "sentry"]
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
    monkeypatch.setattr(flow, "checkbox_prompt", _mock_checkbox)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)
    monkeypatch.setattr(flow, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="validated", sample_response="OpenSRE ready"),
    )
    monkeypatch.setattr(
        flow,
        "validate_github_mcp_integration",
        lambda **_kwargs: flow.IntegrationHealthResult(ok=True, detail="GitHub MCP ok"),
    )
    monkeypatch.setattr(
        flow,
        "validate_sentry_integration",
        lambda **_kwargs: flow.IntegrationHealthResult(ok=True, detail="Sentry ok"),
    )
    monkeypatch.setattr(flow, "save_local_config", lambda **_kwargs: tmp_path / "opensre.json")
    monkeypatch.setattr(flow, "sync_provider_env", lambda **_kwargs: tmp_path / ".env")

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
                    "toolsets": ["repos", "issues", "pull_requests", "actions"],
                }
            },
        ),
        (
            "sentry",
            {
                "credentials": {
                    "base_url": flow.DEFAULT_SENTRY_URL,
                    "organization_slug": "demo-org",
                    "auth_token": "sntrys_test",
                    "project_slug": "payments",
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
            "GITHUB_MCP_AUTH_TOKEN": "ghp_test",
            "GITHUB_MCP_TOOLSETS": "repos,issues,pull_requests,actions",
        },
        {
            "SENTRY_URL": flow.DEFAULT_SENTRY_URL,
            "SENTRY_ORG_SLUG": "demo-org",
            "SENTRY_PROJECT_SLUG": "payments",
            "SENTRY_AUTH_TOKEN": "sntrys_test",
        },
    ]

    output = capsys.readouterr().out
    assert "GitHub MCP" in output
    assert "Sentry" in output


def test_run_wizard_reuses_saved_defaults_when_user_confirms_defaults(monkeypatch, tmp_path) -> None:
    saved: dict[str, object] = {}

    def _mock_select(*_args, choices=None, default=None, **_kwargs):
        m = MagicMock()
        selected_value = default
        if choices is not None:
            for choice in choices:
                if getattr(choice, "title", None) == default:
                    selected_value = choice.value
                    break
        m.ask.return_value = selected_value
        return m

    def _mock_checkbox(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = []
        return m

    def _mock_password(*_args, default="", **_kwargs):
        m = MagicMock()
        m.ask.return_value = default
        return m

    monkeypatch.setattr(flow, "select_prompt", _mock_select)
    monkeypatch.setattr(flow, "checkbox_prompt", _mock_checkbox)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        flow,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "openai",
                    "model": "gpt-5-mini",
                    "api_key_env": "OPENAI_API_KEY",
                    "api_key": "saved-secret",
                }
            },
        },
    )
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="validated", sample_response="ready"),
    )
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

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert saved["wizard_mode"] == "quickstart"
    assert saved["provider"] == "openai"
    assert saved["model"] == "gpt-5-mini"
    assert saved["api_key"] == "saved-secret"


def test_run_wizard_uses_saved_matching_provider_key_without_prompt(monkeypatch, tmp_path, capsys) -> None:
    saved: dict[str, object] = {}
    password_called = False
    select_responses = iter(["quickstart", "anthropic", "claude-opus-4-20250514"])

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_checkbox(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = []
        return m

    def _mock_password(*_args, **_kwargs):
        nonlocal password_called
        password_called = True
        m = MagicMock()
        m.ask.return_value = "should-not-be-used"
        return m

    monkeypatch.setattr(flow, "select_prompt", _mock_select)
    monkeypatch.setattr(flow, "checkbox_prompt", _mock_checkbox)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        flow,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "anthropic",
                    "model": "claude-opus-4-20250514",
                    "api_key_env": "ANTHROPIC_API_KEY",
                    "api_key": "saved-anthropic-key",
                }
            },
        },
    )
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow,
        "validate_provider_credentials",
        lambda **kwargs: ValidationResult(
            ok=True,
            detail=f"validated {kwargs['api_key']}",
            sample_response="ready",
        ),
    )
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

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert password_called is False
    assert saved["api_key"] == "saved-anthropic-key"
    output = capsys.readouterr().out
    assert "Using saved Anthropic key." in output


def test_run_wizard_does_not_reuse_saved_key_for_different_provider(monkeypatch, tmp_path) -> None:
    saved: dict[str, object] = {}
    password_calls: list[str] = []

    select_responses = iter(["quickstart", "anthropic", "claude-opus-4-20250514"])

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_checkbox(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = []
        return m

    def _mock_password(*_args, **_kwargs):
        password_calls.append("called")
        m = MagicMock()
        m.ask.return_value = "fresh-anthropic-key"
        return m

    monkeypatch.setattr(flow, "select_prompt", _mock_select)
    monkeypatch.setattr(flow, "checkbox_prompt", _mock_checkbox)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow, "get_store_path", lambda: tmp_path / "opensre.json")
    monkeypatch.setattr(
        flow,
        "load_local_config",
        lambda _path: {
            "wizard": {"mode": "quickstart"},
            "targets": {
                "local": {
                    "provider": "openai",
                    "model": "gpt-5-mini",
                    "api_key_env": "OPENAI_API_KEY",
                    "api_key": "saved-openai-key",
                }
            },
        },
    )
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="validated", sample_response="ready"),
    )
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

    exit_code = flow.run_wizard()

    assert exit_code == 0
    assert password_calls == ["called"]
    assert saved["api_key"] == "fresh-anthropic-key"


def test_run_wizard_persists_matching_local_config_and_env(monkeypatch, tmp_path) -> None:
    select_responses = iter(["quickstart", "openai", "gpt-5-mini"])

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_checkbox(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = []
        return m

    def _mock_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = "openai-secret"
        return m

    store_path = tmp_path / "opensre.json"
    env_path = tmp_path / ".env"

    monkeypatch.setattr(flow, "select_prompt", _mock_select)
    monkeypatch.setattr(flow, "checkbox_prompt", _mock_checkbox)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow, "get_store_path", lambda: store_path)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="validated", sample_response="ready"),
    )
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

    exit_code = flow.run_wizard()

    assert exit_code == 0

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    env_values = env_path.read_text(encoding="utf-8")

    assert payload["wizard"]["mode"] == "quickstart"
    assert payload["targets"]["local"]["provider"] == "openai"
    assert payload["targets"]["local"]["model"] == "gpt-5-mini"
    assert payload["targets"]["local"]["api_key_env"] == "OPENAI_API_KEY"
    assert payload["targets"]["local"]["model_env"] == "OPENAI_REASONING_MODEL"
    assert payload["targets"]["local"]["api_key"] == "openai-secret"

    assert "LLM_PROVIDER=openai\n" in env_values
    assert "OPENAI_API_KEY=openai-secret\n" in env_values
    assert "OPENAI_REASONING_MODEL=gpt-5-mini\n" in env_values
    assert "OPENAI_MODEL=gpt-5-mini\n" in env_values


def test_run_wizard_switches_provider_and_keeps_store_and_env_in_sync(monkeypatch, tmp_path) -> None:
    select_responses = iter(["quickstart", "openai", "gpt-5-mini"])

    def _mock_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_responses)
        return m

    def _mock_checkbox(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = []
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
        model="claude-opus-4-20250514",
        api_key_env="ANTHROPIC_API_KEY",
        model_env="ANTHROPIC_MODEL",
        api_key="saved-anthropic-key",
        probes={
            "local": {"target": "local", "reachable": True, "detail": "ok"},
            "remote": {"target": "remote", "reachable": False, "detail": "down"},
        },
        path=store_path,
    )
    env_path.write_text(
        "LLM_PROVIDER=anthropic\n"
        "ANTHROPIC_API_KEY=saved-anthropic-key\n"
        "ANTHROPIC_MODEL=claude-opus-4-20250514\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(flow, "select_prompt", _mock_select)
    monkeypatch.setattr(flow, "checkbox_prompt", _mock_checkbox)
    monkeypatch.setattr(flow.questionary, "password", _mock_password)
    monkeypatch.setattr(flow, "get_store_path", lambda: store_path)
    monkeypatch.setattr(flow, "probe_local_target", lambda _path: ProbeResult("local", True, "ok"))
    monkeypatch.setattr(
        flow,
        "validate_provider_credentials",
        lambda **_kwargs: ValidationResult(ok=True, detail="validated", sample_response="ready"),
    )
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

    exit_code = flow.run_wizard()

    assert exit_code == 0

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    env_values = env_path.read_text(encoding="utf-8")

    assert payload["targets"]["local"]["provider"] == "openai"
    assert payload["targets"]["local"]["model"] == "gpt-5-mini"
    assert payload["targets"]["local"]["api_key_env"] == "OPENAI_API_KEY"
    assert payload["targets"]["local"]["model_env"] == "OPENAI_REASONING_MODEL"
    assert payload["targets"]["local"]["api_key"] == "fresh-openai-key"

    assert "LLM_PROVIDER=openai\n" in env_values
    assert "OPENAI_API_KEY=fresh-openai-key\n" in env_values
    assert "OPENAI_REASONING_MODEL=gpt-5-mini\n" in env_values
    assert "OPENAI_MODEL=gpt-5-mini\n" in env_values
