from __future__ import annotations

from typing import Any

from _pytest.monkeypatch import MonkeyPatch

from app.entrypoints.mcp import RunRCAOutput, run_rca


def test_run_rca_malformed_input() -> None:
    result = run_rca(alert_payload="not-a-dict")  # type: ignore[arg-type]

    assert result["ok"] is False
    assert result["result"] is None
    assert result["error"]
    assert result["error_type"] == "ValidationError"


def test_run_rca_happy_path(monkeypatch: MonkeyPatch) -> None:
    def fake_run_cli(
        payload: dict[str, Any],
        *,
        alert_name: str | None = None,
        pipeline_name: str | None = None,
        severity: str | None = None,
    ) -> dict[str, Any]:
        return {
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

    monkeypatch.setattr("app.entrypoints.mcp._run_cli", fake_run_cli)

    payload: dict[str, Any] = {
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
    assert result["error_type"] is None
    assert result["result"] is not None

    response = result["result"]
    assert response["root_cause"] == "High CPU usage"
    assert response["metadata"]["alert_name"] == "HighCPU"
    assert response["metadata"]["pipeline_name"] == "prod-pipeline"
    assert response["metadata"]["severity"] == "critical"
    assert response["payload_seen"]["commonLabels"]["alertname"] == "HighCPU"
    assert response["payload_seen"]["commonLabels"]["pipeline_name"] == "prod-pipeline"
    assert response["payload_seen"]["commonLabels"]["severity"] == "critical"


def test_run_rca_unexpected_exception_includes_error_type(monkeypatch: MonkeyPatch) -> None:
    def fake_run_cli(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("something went wrong")

    monkeypatch.setattr("app.entrypoints.mcp._run_cli", fake_run_cli)

    payload: dict[str, Any] = {"title": "test", "state": "firing", "alert_source": "grafana"}
    result = run_rca(alert_payload=payload)

    assert result["ok"] is False
    assert result["error"] == "something went wrong"
    assert result["error_type"] == "RuntimeError"
    assert result["result"] is None


def test_run_rca_error_type_reflects_actual_exception_class(monkeypatch: MonkeyPatch) -> None:
    def fake_run_cli(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise ValueError("bad value")

    monkeypatch.setattr("app.entrypoints.mcp._run_cli", fake_run_cli)

    payload: dict[str, Any] = {"title": "test", "state": "firing", "alert_source": "grafana"}
    result = run_rca(alert_payload=payload)

    assert result["ok"] is False
    assert result["error_type"] == "ValueError"


def test_run_rca_output_shape_on_success(monkeypatch: MonkeyPatch) -> None:
    """Success response always has ok, result, error, and error_type keys."""
    monkeypatch.setattr("app.entrypoints.mcp._run_cli", lambda *_a, **_kw: {"report": "done"})

    payload: dict[str, Any] = {"title": "test", "state": "firing", "alert_source": "grafana"}
    result = run_rca(alert_payload=payload)

    assert set(result.keys()) >= {"ok", "result", "error", "error_type"}
    assert result["ok"] is True
    assert result["error"] is None
    assert result["error_type"] is None


def test_run_rca_output_shape_on_error() -> None:
    """Error response always has ok, result, error, and error_type keys."""
    result = run_rca(alert_payload="not-a-dict")  # type: ignore[arg-type]

    assert set(result.keys()) >= {"ok", "result", "error", "error_type"}
    assert result["ok"] is False
    assert result["error"] is not None
    assert result["error_type"] is not None


def test_run_rca_output_model_has_error_type_field() -> None:
    """RunRCAOutput model includes error_type in its schema."""
    fields = RunRCAOutput.model_fields
    assert "error_type" in fields


def test_run_rca_output_model_error_type_defaults_to_none() -> None:
    out = RunRCAOutput(ok=True)
    assert out.error_type is None
