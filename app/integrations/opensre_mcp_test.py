from __future__ import annotations

from app.integrations.opensre_mcp import run_rca


def test_run_rca_malformed_input() -> None:
    result = run_rca(alert_payload="not-a-dict")  # type: ignore[arg-type]

    assert result["ok"] is False
    assert result["result"] is None
    assert result["error"]


def test_run_rca_happy_path(monkeypatch) -> None:
    def fake_run_cli(
        payload: dict,
        *,
        alert_name: str | None = None,
        pipeline_name: str | None = None,
        severity: str | None = None,
    ) -> dict:
        return {
            "slack_message": "RCA complete",
            "report": "RCA complete",
            "problem_md": "# Alert\n\nCPU high",
            "root_cause": "High CPU usage",
            "payload_seen": payload,
            "metadata": {
                "alert_name": alert_name,
                "pipeline_name": pipeline_name,
                "severity": severity,
            },
        }

    monkeypatch.setattr("app.integrations.opensre_mcp._run_cli", fake_run_cli)

    payload = {
        "title": "CPU alert",
        "state": "firing",
        "alert_source": "grafana",
        "commonLabels": {},
        "commonAnnotations": {"summary": "CPU high"},
    }

    result = run_rca(
        alert_payload=payload,
        alert_name="HighCPU",
        pipeline_name="prod-pipeline",
        severity="critical",
    )

    assert result["ok"] is True
    assert result["error"] is None
    assert result["result"] is not None

    response = result["result"]
    assert response["root_cause"] == "High CPU usage"
    assert response["metadata"]["alert_name"] == "HighCPU"
    assert response["metadata"]["pipeline_name"] == "prod-pipeline"
    assert response["metadata"]["severity"] == "critical"
    assert response["payload_seen"]["commonLabels"]["alertname"] == "HighCPU"
    assert response["payload_seen"]["commonLabels"]["pipeline_name"] == "prod-pipeline"
    assert response["payload_seen"]["commonLabels"]["severity"] == "critical"
