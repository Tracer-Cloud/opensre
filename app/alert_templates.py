"""Starter alert payload templates for local and standalone investigations."""

from __future__ import annotations

from typing import Any


def build_alert_template(template_name: str) -> dict[str, Any]:
    """Return a starter alert payload template by name."""
    template = template_name.strip().lower()

    if template == "generic":
        return {
            "alert_name": "High error rate in payments ETL",
            "pipeline_name": "payments_etl",
            "severity": "critical",
            "alert_source": "generic",
            "message": "payments_etl is failing with repeated database connection errors",
            "commonAnnotations": {
                "summary": "payments_etl is failing with repeated database connection errors",
                "correlation_id": "replace-me",
            },
        }

    if template == "datadog":
        return {
            "title": "[Triggered] payments-etl error rate high",
            "alert_name": "Datadog monitor: payments-etl error rate high",
            "pipeline_name": "payments_etl",
            "severity": "critical",
            "alert_source": "datadog",
            "message": "Datadog monitor detected repeated errors in payments_etl",
            "text": "payments_etl is failing in production",
            "commonLabels": {
                "pipeline_name": "payments_etl",
                "severity": "critical",
            },
            "commonAnnotations": {
                "summary": "payments_etl is failing in production",
                "query": "service:payments-etl status:error",
                "kube_namespace": "payments",
                "correlation_id": "replace-me",
            },
        }

    if template == "grafana":
        return {
            "title": "[FIRING:1] Pipeline failure rate high - payments_etl",
            "alert_name": "Grafana alert: Pipeline failure rate high",
            "pipeline_name": "payments_etl",
            "severity": "critical",
            "alert_source": "grafana",
            "state": "alerting",
            "externalURL": "https://your-grafana-instance.grafana.net",
            "commonLabels": {
                "alertname": "PipelineFailureRateHigh",
                "severity": "critical",
                "pipeline_name": "payments_etl",
                "grafana_folder": "production-pipelines",
            },
            "commonAnnotations": {
                "summary": "payments_etl stopped updating after repeated failures",
                "source_url": "https://your-grafana-instance.grafana.net/explore",
                "execution_run_id": "replace-me",
                "correlation_id": "replace-me",
            },
        }

    raise ValueError(
        "Unknown alert template. Supported templates: generic, datadog, grafana."
    )
