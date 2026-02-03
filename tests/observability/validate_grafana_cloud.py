#!/usr/bin/env python3
"""Validate local code against Grafana Cloud endpoints.

This script:
1. Reads Grafana Cloud credentials from AWS Secrets Manager (tracer/grafana-cloud)
2. Runs pipelines locally but points to Grafana Cloud endpoints
3. Queries Grafana Cloud Mimir API for metrics
4. Queries Grafana Cloud Loki API for logs
5. Validates execution_run_id appears in both
6. Generates validation report
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import boto3
import requests


def get_grafana_secrets() -> dict[str, str]:
    """Retrieve Grafana Cloud secrets from AWS Secrets Manager."""
    secrets_client = boto3.client("secretsmanager")
    try:
        response = secrets_client.get_secret_value(SecretId="tracer/grafana-cloud")
        secret_string = response["SecretString"]
        return json.loads(secret_string)
    except Exception as e:
        print(f"Failed to retrieve secrets: {e}")
        print("Falling back to environment variables...")
        return {
            "GCLOUD_HOSTED_METRICS_ID": os.getenv("GCLOUD_HOSTED_METRICS_ID", ""),
            "GCLOUD_HOSTED_METRICS_URL": os.getenv("GCLOUD_HOSTED_METRICS_URL", ""),
            "GCLOUD_HOSTED_LOGS_ID": os.getenv("GCLOUD_HOSTED_LOGS_ID", ""),
            "GCLOUD_HOSTED_LOGS_URL": os.getenv("GCLOUD_HOSTED_LOGS_URL", ""),
            "GCLOUD_RW_API_KEY": os.getenv("GCLOUD_RW_API_KEY", ""),
            "GCLOUD_OTLP_ENDPOINT": os.getenv("GCLOUD_OTLP_ENDPOINT", ""),
            "GCLOUD_OTLP_AUTH_HEADER": os.getenv("GCLOUD_OTLP_AUTH_HEADER", ""),
        }


def run_command(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Run a shell command and return exit code, stdout, stderr."""
    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, env=env, check=False
    )
    return result.returncode, result.stdout, result.stderr


def run_prefect_flow_with_cloud(secrets: dict[str, str]) -> str | None:
    """Run Prefect flow locally with Grafana Cloud endpoints."""
    print("Running Prefect flow locally with Grafana Cloud endpoints...")
    prefect_dir = Path(__file__).parent.parent / "test_case_upstream_prefect_ecs_fargate"
    flow_file = prefect_dir / "pipeline_code" / "prefect_flow" / "flow.py"

    env = os.environ.copy()
    env.update(secrets)
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = secrets.get("GCLOUD_OTLP_ENDPOINT", "")
    env["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization={secrets.get('GCLOUD_OTLP_AUTH_HEADER', '')}"
    env["OTEL_EXPORTER_OTLP_PROTOCOL"] = "grpc"

    exit_code, stdout, stderr = run_command(
        ["python3", str(flow_file), "test-bucket", "test-key"],
        cwd=prefect_dir,
        env=env,
    )
    return "prefect-cloud-test-run"


def query_grafana_mimir(secrets: dict[str, str], query: str) -> list[dict[str, Any]]:
    """Query Grafana Cloud Mimir for metrics."""
    metrics_url = secrets.get("GCLOUD_HOSTED_METRICS_URL", "")
    api_key = secrets.get("GCLOUD_RW_API_KEY", "")

    if not metrics_url or not api_key:
        print("Missing Mimir credentials")
        return []

    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        response = requests.get(
            metrics_url.replace("/api/prom/push", "/api/v1/query"),
            params={"query": query},
            headers=headers,
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("data", {}).get("result", [])
    except requests.RequestException as e:
        print(f"Mimir query failed: {e}")
    return []


def query_grafana_loki(secrets: dict[str, str], query: str) -> list[dict[str, Any]]:
    """Query Grafana Cloud Loki for logs."""
    logs_url = secrets.get("GCLOUD_HOSTED_LOGS_URL", "")
    api_key = secrets.get("GCLOUD_RW_API_KEY", "")

    if not logs_url or not api_key:
        print("Missing Loki credentials")
        return []

    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        loki_query_url = logs_url.replace("/loki/api/v1/push", "/loki/api/v1/query")
        response = requests.get(
            loki_query_url,
            params={"query": query, "limit": 100},
            headers=headers,
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("data", {}).get("result", [])
    except requests.RequestException as e:
        print(f"Loki query failed: {e}")
    return []


def validate_cloud_telemetry(secrets: dict[str, str], execution_run_id: str) -> dict[str, Any]:
    """Validate that telemetry appears in Grafana Cloud."""
    results = {
        "execution_run_id": execution_run_id,
        "logs_found": False,
        "metrics_found": False,
        "log_count": 0,
        "metric_count": 0,
    }

    print(f"Querying Grafana Cloud Loki for execution_run_id={execution_run_id}...")
    log_query = f'{{service_name=~".*"}} |= "{execution_run_id}"'
    logs = query_grafana_loki(secrets, log_query)
    results["log_count"] = len(logs)
    results["logs_found"] = len(logs) > 0

    print("Querying Grafana Cloud Mimir for metrics...")
    metric_query = 'pipeline_runs_total'
    metrics = query_grafana_mimir(secrets, metric_query)
    results["metric_count"] = len(metrics)
    results["metrics_found"] = len(metrics) > 0

    return results


def generate_report(results: list[dict[str, Any]]) -> None:
    """Generate validation report."""
    print("\n" + "=" * 60)
    print("GRAFANA CLOUD VALIDATION REPORT")
    print("=" * 60)

    for result in results:
        print(f"\nExecution Run ID: {result['execution_run_id']}")
        print(f"  Logs: {'✓' if result['logs_found'] else '✗'} ({result['log_count']} found)")
        print(f"  Metrics: {'✓' if result['metrics_found'] else '✗'} ({result['metric_count']} found)")

    all_passed = all(
        r["logs_found"] and r["metrics_found"]
        for r in results
    )

    print("\n" + "=" * 60)
    if all_passed:
        print("✓ ALL VALIDATIONS PASSED")
    else:
        print("✗ SOME VALIDATIONS FAILED")
    print("=" * 60)


def main() -> int:
    """Main entry point."""
    print("Retrieving Grafana Cloud credentials...")
    secrets = get_grafana_secrets()

    required_vars = [
        "GCLOUD_OTLP_ENDPOINT",
        "GCLOUD_OTLP_AUTH_HEADER",
        "GCLOUD_HOSTED_METRICS_URL",
        "GCLOUD_HOSTED_LOGS_URL",
        "GCLOUD_RW_API_KEY",
    ]

    missing = [var for var in required_vars if not secrets.get(var)]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}")
        return 1

    print("Running test pipelines with Grafana Cloud endpoints...")

    execution_run_ids = []

    run_id = run_prefect_flow_with_cloud(secrets)
    if run_id:
        execution_run_ids.append(run_id)
        print("Waiting for telemetry export...")
        time.sleep(10)

    results = []
    for run_id in execution_run_ids:
        result = validate_cloud_telemetry(secrets, run_id)
        results.append(result)

    generate_report(results)

    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
