from __future__ import annotations

from app.cli.wizard import flow
from app.cli.wizard.probes import ProbeResult
from app.cli.wizard.validation import ValidationResult


def test_run_wizard_advanced_remote_falls_back_to_local(monkeypatch, tmp_path, capsys) -> None:
    responses = iter(["2", "2", "y", "", ""])
    saved: dict[str, object] = {}

    monkeypatch.setattr("builtins.input", lambda _prompt="": next(responses))
    monkeypatch.setattr(flow.getpass, "getpass", lambda _prompt="": "secret-key")
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
    assert "Remote configuration is not available yet." in output
    assert "Provider sample: OpenSRE ready" in output
    assert "Demo action response" in output
    assert "Saved configuration" in output
    assert "config" in output


def test_run_wizard_retries_invalid_api_key(monkeypatch, tmp_path, capsys) -> None:
    responses = iter(["", "", ""])
    validations = iter(
        [
            ValidationResult(ok=False, detail="bad key"),
            ValidationResult(ok=True, detail="validated", sample_response="OpenSRE ready"),
        ]
    )

    monkeypatch.setattr("builtins.input", lambda _prompt="": next(responses))
    monkeypatch.setattr(flow.getpass, "getpass", lambda _prompt="": "secret-key")
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
    assert "Paste the API key again to retry" in output
    assert "validated" in output
