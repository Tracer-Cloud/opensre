"""Alerting adapter for pipeline failures.

This module is designed to run inside ECS containers without external test dependencies.
"""

import os
import uuid
from datetime import UTC, datetime


def _create_alert_payload(
    pipeline_name: str,
    run_name: str,
    status: str,
    timestamp: str,
    annotations: dict,
    severity: str = "critical",
    alert_name: str = "PipelineFailure",
) -> dict:
    """Create a Grafana-style alert payload (inlined to avoid test dependencies)."""
    alert_id = str(uuid.uuid4())
    return {
        "alert_id": alert_id,
        "status": status,
        "labels": {
            "alertname": alert_name,
            "severity": severity,
            "pipeline": pipeline_name,
            "run_name": run_name,
            "environment": "production",
        },
        "annotations": {
            "summary": f"Pipeline {pipeline_name} {status}",
            "description": f"Run {run_name} has status: {status}",
            "timestamp": timestamp,
            **annotations,
        },
        "startsAt": timestamp,
        "generatorURL": "",
    }


def fire_pipeline_alert(
    pipeline_name: str, bucket: str, key: str, correlation_id: str, error: Exception
):
    """Standardized alerting for pipeline failures.

    Note: This currently just logs the alert. In production, this would
    fire to a LangGraph endpoint or alerting system.
    """
    run_id = f"run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

    alert_payload = _create_alert_payload(
        pipeline_name=pipeline_name,
        run_name=run_id,
        status="failed",
        timestamp=datetime.now(UTC).isoformat(),
        annotations={
            "landing_bucket": bucket,
            "s3_key": key,
            "correlation_id": correlation_id,
            "error": str(error),
            "error_type": type(error).__name__,
            "context_sources": "s3,prefect,ecs",
            "prefect_flow": "upstream_downstream_pipeline",
            "ecs_cluster": "tracer-prefect-cluster",
            "cloudwatch_log_group": "/ecs/tracer-prefect",
            "processed_bucket": os.getenv("PROCESSED_BUCKET", "processed-bucket"),
        },
    )

    # Log the alert (in production, this would fire to LangGraph)
    print("ALERT: Pipeline failure detected")
    print(f"  Pipeline: {pipeline_name}")
    print(f"  Correlation ID: {correlation_id}")
    print(f"  Error: {error}")
    print(f"  Alert ID: {alert_payload['alert_id']}")
