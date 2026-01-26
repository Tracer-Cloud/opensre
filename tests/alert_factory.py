"""
Alert factory for creating Grafana-style alerts from various sources.

This module provides builders/factories for creating alert payloads,
separating alert creation logic from test and demo flows.

This factory is pre-LLM and pre-LangGraph - it only creates standard
Grafana alert payload structures without any Tracer-specific context.
"""

from typing import Any


class AlertBuilder:
    """Builder for creating Grafana-style alert payloads."""

    def __init__(self, external_url: str = "") -> None:
        """
        Initialize a new alert builder.

        Args:
            external_url: Optional external URL for the alerting system
        """
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
        """
        Build alert from Tracer pipeline run data.

        Args:
            pipeline_name: Name of the pipeline
            run_name: Name of the run
            status: Status of the run (e.g., "failed", "error")
            timestamp: ISO timestamp string (e.g., "2026-01-27T12:00:00Z")
            trace_id: Optional trace ID for the run
            run_url: Optional URL to view the run

        Returns:
            Self for method chaining
        """
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
        self._alert["commonAnnotations"] = {"summary": f"Pipeline {pipeline_name} failed"}
        self._alert["groupKey"] = f'{{}}:{{alertname="{alertname}"}}'
        self._alert["title"] = f"[FIRING:1] {alertname} {severity} - {pipeline_name}"
        self._alert["state"] = "alerting"
        self._alert["message"] = (
            f"**Firing**\n\nPipeline {pipeline_name} failed\n"
            f"Run: {run_name}\nStatus: {status}\nTrace ID: {trace_id}"
        )

        return self

    def build(self) -> dict[str, Any]:
        """
        Build and return the final alert payload.

        Returns:
            Complete Grafana-style alert payload dictionary
        """
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
    """
    Pure function to create a Grafana-style alert from pipeline run data.

    This creates a standard Grafana alert payload without any Tracer-specific
    top-level fields. All context is embedded in the standard Grafana structure
    (labels, annotations, etc.).

    This is a pure function - given the same inputs, it always produces the same output.

    Args:
        pipeline_name: Name of the pipeline
        run_name: Name of the run
        status: Status of the run (e.g., "failed", "error")
        timestamp: ISO timestamp string (e.g., "2026-01-27T12:00:00Z")
        trace_id: Optional trace ID for the run (used in labels/fingerprint)
        run_url: Optional URL to view the run (used in annotations/generatorURL)
        external_url: Optional external URL for the alerting system

    Returns:
        Complete Grafana-style alert payload dictionary (standard format only)
    """
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
