from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.cli.investigate import resolve_investigation_context, run_investigation_cli


def test_resolve_investigation_context_prefers_cli_overrides() -> None:
    alert_name, pipeline_name, severity = resolve_investigation_context(
        raw_alert={
            "alert_name": "PayloadAlert",
            "pipeline_name": "payload_pipeline",
            "severity": "warning",
        },
        alert_name="CliAlert",
        pipeline_name="cli_pipeline",
        severity="critical",
    )

    assert alert_name == "CliAlert"
    assert pipeline_name == "cli_pipeline"
    assert severity == "critical"


def test_run_investigation_cli_shapes_agent_state(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_investigation(
        alert_name: str,
        pipeline_name: str,
        severity: str,
        *,
        raw_alert: dict[str, object],
    ) -> dict[str, object]:
        captured["alert_name"] = alert_name
        captured["pipeline_name"] = pipeline_name
        captured["severity"] = severity
        captured["raw_alert"] = raw_alert
        return {
            "slack_message": "report body",
            "problem_md": "# problem",
            "root_cause": "bad deploy",
        }

    monkeypatch.setattr("app.cli.investigate.LLMSettings.from_env", lambda: object())
    monkeypatch.setattr("app.cli.investigate._call_run_investigation", fake_run_investigation)

    result = run_investigation_cli(
        raw_alert={"alert_name": "PayloadAlert"},
        alert_name=None,
        pipeline_name=None,
        severity=None,
    )

    assert captured == {
        "alert_name": "PayloadAlert",
        "pipeline_name": "events_fact",
        "severity": "warning",
        "raw_alert": {"alert_name": "PayloadAlert"},
    }
    assert result == {
        "slack_message": "report body",
        "report": "report body",
        "problem_md": "# problem",
        "root_cause": "bad deploy",
    }


def test_run_investigation_cli_fails_fast_for_invalid_llm_config(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.cli.investigate._call_run_investigation",
        lambda *_args, **_kwargs: pytest.fail("investigation should not start"),
    )

    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        run_investigation_cli(raw_alert={"alert_name": "PayloadAlert"})
