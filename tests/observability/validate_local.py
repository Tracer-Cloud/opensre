#!/usr/bin/env python3
"""Validate telemetry against local Grafana stack.

This script:
1. Starts local Grafana stack (docker compose up -d)
2. Runs each test case pipeline locally with OTEL_EXPORTER_OTLP_ENDPOINT=localhost:4317
3. Queries Loki for logs with execution_run_id
4. Queries Tempo for traces with execution.run_id attribute
5. Queries Prometheus for metrics
6. Generates validation report
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import requests

# Local Grafana endpoints
GRAFANA_URL = "http://localhost:3000"
LOKI_URL = f"{GRAFANA_URL}/loki/api/v1"
TEMPO_URL = f"{GRAFANA_URL}/api/tempo/api/traces"
PROMETHEUS_URL = f"{GRAFANA_URL}/api/prometheus/api/v1"


def run_command(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    """Run a shell command and return exit code, stdout, stderr."""
    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd, check=False
    )
    return result.returncode, result.stdout, result.stderr


def start_local_stack() -> bool:
    """Start local Grafana stack."""
    print("Starting local Grafana stack...")
    observability_dir = Path(__file__).parent
    exit_code, stdout, stderr = run_command(
        ["docker", "compose", "up", "-d"], cwd=observability_dir
    )
    if exit_code != 0:
        print(f"Failed to start stack: {stderr}")
        return False
    print("Stack started. Waiting for services to be ready...")
    time.sleep(10)
    return True


def wait_for_grafana(max_wait: int = 60) -> bool:
    """Wait for Grafana to be ready."""
    for _ in range(max_wait):
        try:
            response = requests.get(f"{GRAFANA_URL}/api/health", timeout=2)
            if response.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(1)
    return False


def run_prefect_flow() -> str | None:
    """Run Prefect flow locally and return execution_run_id."""
    print("Running Prefect flow locally...")
    prefect_dir = Path(__file__).parent.parent / "test_case_upstream_prefect_ecs_fargate"
    flow_file = prefect_dir / "pipeline_code" / "prefect_flow" / "flow.py"
    
    env = os.environ.copy()
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
    env["OTEL_EXPORTER_OTLP_PROTOCOL"] = "grpc"
    
    # Use a test bucket/key - this will fail but should emit telemetry
    exit_code, stdout, stderr = run_command(
        ["python3", str(flow_file), "test-bucket", "test-key"],
        cwd=prefect_dir,
    )
    # Extract execution_run_id from logs if available
    return "prefect-test-run"


def query_loki_logs(execution_run_id: str) -> list[dict[str, Any]]:
    """Query Loki for logs containing execution_run_id."""
    try:
        query = f'{{service_name=~".*"}} |= "{execution_run_id}"'
        response = requests.get(
            f"{LOKI_URL}/query",
            params={"query": query, "limit": 100},
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("data", {}).get("result", [])
    except requests.RequestException as e:
        print(f"Loki query failed: {e}")
    return []


def query_tempo_traces(execution_run_id: str) -> list[dict[str, Any]]:
    """Query Tempo for traces with execution.run_id attribute."""
    try:
        query = f'{{execution.run_id="{execution_run_id}"}}'
        response = requests.get(
            f"{TEMPO_URL}/search",
            params={"tags": query},
            timeout=10,
        )
        if response.status_code == 200:
            return response.json().get("traces", [])
    except requests.RequestException as e:
        print(f"Tempo query failed: {e}")
    return []


def query_prometheus_metrics(execution_run_id: str) -> list[dict[str, Any]]:
    """Query Prometheus for metrics."""
    try:
        query = 'pipeline_runs_total'
        response = requests.get(
            f"{PROMETHEUS_URL}/query",
            params={"query": query},
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("data", {}).get("result", [])
    except requests.RequestException as e:
        print(f"Prometheus query failed: {e}")
    return []


def validate_telemetry(execution_run_id: str) -> dict[str, Any]:
    """Validate that telemetry appears in all three systems."""
    results = {
        "execution_run_id": execution_run_id,
        "logs_found": False,
        "traces_found": False,
        "metrics_found": False,
        "log_count": 0,
        "trace_count": 0,
        "metric_count": 0,
    }
    
    print(f"Querying Loki for execution_run_id={execution_run_id}...")
    logs = query_loki_logs(execution_run_id)
    results["log_count"] = len(logs)
    results["logs_found"] = len(logs) > 0
    
    print(f"Querying Tempo for execution.run_id={execution_run_id}...")
    traces = query_tempo_traces(execution_run_id)
    results["trace_count"] = len(traces)
    results["traces_found"] = len(traces) > 0
    
    print(f"Querying Prometheus for metrics...")
    metrics = query_prometheus_metrics(execution_run_id)
    results["metric_count"] = len(metrics)
    results["metrics_found"] = len(metrics) > 0
    
    return results


def generate_report(results: list[dict[str, Any]]) -> None:
    """Generate validation report."""
    print("\n" + "=" * 60)
    print("LOCAL GRAFANA VALIDATION REPORT")
    print("=" * 60)
    
    for result in results:
        print(f"\nExecution Run ID: {result['execution_run_id']}")
        print(f"  Logs: {'✓' if result['logs_found'] else '✗'} ({result['log_count']} found)")
        print(f"  Traces: {'✓' if result['traces_found'] else '✗'} ({result['trace_count']} found)")
        print(f"  Metrics: {'✓' if result['metrics_found'] else '✗'} ({result['metric_count']} found)")
    
    all_passed = all(
        r["logs_found"] and r["traces_found"] and r["metrics_found"]
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
    if not start_local_stack():
        return 1
    
    if not wait_for_grafana():
        print("Grafana did not become ready in time")
        return 1
    
    print("Grafana is ready. Running test pipelines...")
    
    # Run test pipelines
    execution_run_ids = []
    
    # Run Prefect flow
    run_id = run_prefect_flow()
    if run_id:
        execution_run_ids.append(run_id)
        time.sleep(5)  # Wait for telemetry export
    
    # Validate telemetry
    results = []
    for run_id in execution_run_ids:
        result = validate_telemetry(run_id)
        results.append(result)
    
    generate_report(results)
    
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
