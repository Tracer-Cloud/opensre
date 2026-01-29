"""
Alert factory for creating standardized alert payloads.

Provides builders/factories for creating alerts from various sources:
- Tracer pipeline runs (Grafana format)
- CloudWatch log events (simple format)

All factories are pure functions - same inputs produce same outputs.
"""

import uuid
from typing import Any


class AlertBuilder:
    def __init__(self, external_url: str = "") -> None:
        self._alert: dict[str, Any] = {
            "alerts": [],
            "version": "4",
            "externalURL": external_url,
            "truncatedAlerts": 0,
        }

    def from_tracer_run(
        self,
        pipeline_name: str,
        run_name: str,
        status: str,
        timestamp: str,
        trace_id: str | None = None,
        run_url: str | None = None,
    ) -> "AlertBuilder":
        alertname = "PipelineFailure"
        severity = "critical"

        alert = {
            "status": "firing",
            "labels": {
                "alertname": alertname,
                "severity": severity,
                "table": pipeline_name,
                "pipeline_name": pipeline_name,
                "run_id": trace_id or "",
                "run_name": run_name,
                "environment": "production",
            },
            "annotations": {
                "summary": f"Pipeline {pipeline_name} failed",
                "description": f"Pipeline {pipeline_name} run {run_name} failed with status {status}",
                "runbook_url": run_url or "",
            },
            "startsAt": timestamp,
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": run_url or "",
            "fingerprint": trace_id or "unknown",
        }

        self._alert["alerts"] = [alert]
        self._alert["groupLabels"] = {"alertname": alertname}
        self._alert["commonLabels"] = {
            "alertname": alertname,
            "severity": severity,
            "pipeline_name": pipeline_name,
        }
        self._alert["commonAnnotations"] = {
            "summary": f"Pipeline {pipeline_name} failed"
        }
        self._alert["groupKey"] = f'{{}}:{{alertname="{alertname}"}}'
        self._alert["title"] = f"[FIRING:1] {alertname} {severity} - {pipeline_name}"
        self._alert["state"] = "alerting"
        self._alert["message"] = (
            f"**Firing**\n\nPipeline {pipeline_name} failed\n"
            f"Run: {run_name}\nStatus: {status}\nTrace ID: {trace_id}"
        )

        return self

    def build(self) -> dict[str, Any]:
        return self._alert.copy()


def create_alert_from_tracer_run(
    pipeline_name: str,
    run_name: str,
    status: str,
    timestamp: str,
    trace_id: str | None = None,
    run_url: str | None = None,
    external_url: str = "",
) -> dict[str, Any]:
    """Create Grafana-style alert from Tracer pipeline run (pure function)."""
    return (
        AlertBuilder(external_url=external_url)
        .from_tracer_run(
            pipeline_name=pipeline_name,
            run_name=run_name,
            status=status,
            timestamp=timestamp,
            trace_id=trace_id,
            run_url=run_url,
        )
        .build()
    )


def create_alert(
    pipeline_name: str,
    run_name: str,
    status: str,
    timestamp: str,
    annotations: dict[str, Any] | None = None,
    trace_id: str | None = None,
    run_url: str | None = None,
    external_url: str = "",
    alert_id: str | None = None,
) -> dict[str, Any]:
    """
    Create standardized Grafana-style alert (pure function).

    Works for all sources: Tracer, CloudWatch, S3, etc.
    Source-specific metadata goes into annotations (generic).

    Args:
        pipeline_name: Pipeline name
        run_name: Run name/identifier
        status: Status (e.g., "failed")
        timestamp: ISO timestamp
        annotations: Optional custom metadata (CloudWatch logs, S3 paths, etc.)
        trace_id: Optional trace ID
        run_url: Optional run URL
        external_url: Optional external URL
        alert_id: Optional alert ID (generated if not provided)

    Returns:
        Grafana-style alert payload with custom annotations and alert_id
    """
    generated_alert_id = alert_id or str(uuid.uuid4())

    alert = (
        AlertBuilder(external_url=external_url)
        .from_tracer_run(
            pipeline_name=pipeline_name,
            run_name=run_name,
            status=status,
            timestamp=timestamp,
            trace_id=trace_id,
            run_url=run_url,
        )
        .build()
    )

    alert["alert_id"] = generated_alert_id

    if annotations:
        if "commonAnnotations" not in alert:
            alert["commonAnnotations"] = {}
        alert["commonAnnotations"].update(annotations)

    return alert
