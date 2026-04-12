#!/usr/bin/env python3
"""Capture a real Datadog alert + evidence fixture for the K8s RCA feedback test.

Queries the Datadog API for logs and monitors matching the K8s test case,
validates response shapes against the DatadogClient contract, and writes
the fixture to fixtures/datadog_k8s_alert.json.

Prerequisites:
    - K8s error path has been triggered (test_datadog.py or trigger_alert.py)
    - DD_API_KEY and DD_APP_KEY environment variables set

Usage (from project root):
    python -m tests.e2e.kubernetes.capture_fixture
    python -m tests.e2e.kubernetes.capture_fixture --time-range 120
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from tests.utils.alert_factory import create_alert

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "datadog_k8s_alert.json"

LOG_QUERY = "PIPELINE_ERROR kube_namespace:tracer-test"
MONITOR_TAG = "managed_by:tracer-agent"

LOG_ENTRY_SCHEMA: dict[str, type | tuple[type, ...]] = {
    "timestamp": str,
    "message": str,
    "status": str,
    "service": str,
    "host": str,
    "tags": list,
}

MONITOR_SCHEMA: dict[str, type | tuple[type, ...]] = {
    "id": (int, type(None)),
    "name": str,
    "type": str,
    "query": str,
    "message": str,
    "overall_state": str,
    "tags": list,
}


def _validate_shape(entry: dict, schema: dict[str, type | tuple[type, ...]], label: str) -> None:
    for key, expected_type in schema.items():
        if key not in entry:
            raise ValueError(f"{label} missing required key: {key}")
        if not isinstance(entry[key], expected_type):
            raise TypeError(
                f"{label}.{key}: expected {expected_type}, got {type(entry[key]).__name__}"
            )


def _extract_k8s_tags(logs: list[dict]) -> dict[str, str]:
    """Extract K8s metadata from log tags for the alert payload."""
    k8s = {}
    for log in logs:
        for tag in log.get("tags", []):
            if isinstance(tag, str) and ":" in tag:
                key, _, val = tag.partition(":")
                if key.startswith("kube_") and key not in k8s:
                    k8s[key] = val
    return k8s


def capture(time_range_minutes: int = 60) -> dict:
    from app.services.datadog import DatadogClient, DatadogConfig

    api_key = os.environ.get("DD_API_KEY", "")
    app_key = os.environ.get("DD_APP_KEY", "")
    site = os.environ.get("DD_SITE", "datadoghq.com")

    if not api_key or not app_key:
        print("DD_API_KEY and DD_APP_KEY are required")
        sys.exit(1)

    client = DatadogClient(DatadogConfig(api_key=api_key, app_key=app_key, site=site))

    print(f"Querying Datadog logs: {LOG_QUERY} (last {time_range_minutes}min)...")
    log_result = client.search_logs(LOG_QUERY, time_range_minutes=time_range_minutes, limit=50)
    if not log_result.get("success"):
        print(f"Log query failed: {log_result.get('error')}")
        sys.exit(1)

    logs = log_result.get("logs", [])
    if not logs:
        print("No logs found. Has the K8s error path been triggered recently?")
        sys.exit(1)

    print(f"  Found {len(logs)} log entries")
    for i, log in enumerate(logs):
        _validate_shape(log, LOG_ENTRY_SCHEMA, f"log[{i}]")
    print("  All log entries pass schema validation")

    error_keywords = ("error", "fail", "exception", "traceback", "pipeline_error")
    error_logs = [
        log for log in logs if any(kw in log.get("message", "").lower() for kw in error_keywords)
    ]
    print(f"  {len(error_logs)} error logs")

    print(f"\nQuerying Datadog monitors: tag:{MONITOR_TAG}...")
    monitor_result = client.list_monitors(query=f"tag:{MONITOR_TAG}")
    if not monitor_result.get("success"):
        print(f"Monitor query failed: {monitor_result.get('error')}")
        sys.exit(1)

    monitors = monitor_result.get("monitors", [])
    print(f"  Found {len(monitors)} monitors")
    for i, mon in enumerate(monitors):
        _validate_shape(mon, MONITOR_SCHEMA, f"monitor[{i}]")
    print("  All monitors pass schema validation")

    k8s_tags = _extract_k8s_tags(logs)
    kube_namespace = k8s_tags.get("kube_namespace", "tracer-test")
    kube_job = k8s_tags.get("kube_job", "etl-transform-error")

    alert = create_alert(
        pipeline_name="kubernetes_etl_pipeline",
        run_name=kube_job,
        status="failed",
        timestamp=datetime.now(UTC).isoformat(),
        severity="critical",
        alert_name="KubernetesJobFailed",
        environment="test",
        annotations={
            "summary": f"Kubernetes job {kube_job} failed in namespace {kube_namespace}",
            "kube_namespace": kube_namespace,
            "kube_job": kube_job,
        },
    )

    fixture = {
        "_meta": {
            "captured_at": datetime.now(UTC).isoformat(),
            "source": "capture_fixture.py",
            "datadog_site": site,
            "schema_version": 1,
        },
        "alert": alert,
        "evidence": {
            "datadog_logs": logs,
            "datadog_error_logs": error_logs,
            "datadog_monitors": monitors,
        },
    }

    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FIXTURE_PATH, "w") as f:
        json.dump(fixture, f, indent=2)

    print(f"\nFixture written to {FIXTURE_PATH}")
    print(f"  Logs: {len(logs)}, Error logs: {len(error_logs)}, Monitors: {len(monitors)}")
    return fixture


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture Datadog K8s fixture")
    parser.add_argument(
        "--time-range",
        type=int,
        default=60,
        help="How far back to search logs (minutes, default 60)",
    )
    args = parser.parse_args()
    capture(time_range_minutes=args.time_range)
    return 0


if __name__ == "__main__":
    sys.exit(main())
