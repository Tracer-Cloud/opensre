from typing import Any, Dict
from ..intent import AlertIntent

def format_as_grafana(intent: AlertIntent) -> Dict[str, Any]:
    """Renders an AlertIntent into a Grafana/Tracer-compatible payload."""
    alert = {
        "status": "firing",
        "labels": {
            "alertname": intent.alert_name,
            "severity": intent.severity,
            "table": intent.pipeline_name,
            "pipeline_name": intent.pipeline_name,
            "run_id": intent.trace_id or "",
            "run_name": intent.run_name,
            "environment": intent.environment,
        },
        "annotations": {
            "summary": f"Pipeline {intent.pipeline_name} failed",
            "description": f"Pipeline {intent.pipeline_name} run {intent.run_name} failed with status {intent.status}",
            "runbook_url": intent.run_url or "",
        },
        "startsAt": intent.timestamp,
        "endsAt": "0001-01-01T00:00:00Z",
        "generatorURL": intent.run_url or "",
        "fingerprint": intent.trace_id or "unknown",
    }

    payload = {
        "alerts": [alert],
        "version": "4",
        "externalURL": intent.external_url,
        "truncatedAlerts": 0,
        "groupLabels": {"alertname": intent.alert_name},
        "commonLabels": {
            "alertname": intent.alert_name,
            "severity": intent.severity,
            "pipeline_name": intent.pipeline_name,
        },
        "commonAnnotations": {
            "summary": f"Pipeline {intent.pipeline_name} failed"
        },
        "groupKey": f'{{}}:{{alertname="{intent.alert_name}"}}',
        "title": f"[FIRING:1] {intent.alert_name} {intent.severity} - {intent.pipeline_name}",
        "state": "alerting",
        "message": (
            f"**Firing**\n\nPipeline {intent.pipeline_name} failed\n"
            f"Run: {intent.run_name}\nStatus: {intent.status}\nTrace ID: {intent.trace_id}"
        ),
        "alert_id": intent.alert_id,
    }

    if intent.annotations:
        payload["commonAnnotations"].update(intent.annotations)

    return payload
