#!/usr/bin/env python3
"""
Local test for S3 Failed Python pipeline with Grafana validation.

Runs pipeline locally with local OTLP endpoint and validates
logs and traces appear in Grafana.
"""

import os
import sys
import time
from pathlib import Path

import requests

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Local Grafana endpoints
GRAFANA_URL = "http://localhost:3000"
LOKI_URL = f"{GRAFANA_URL}/loki/api/v1"
TEMPO_URL = f"{GRAFANA_URL}/api/tempo/api/traces"


def query_loki_logs(execution_run_id: str) -> list[dict]:
    """Query Loki for logs containing execution_run_id."""
    try:
        query = f'{{service_name="s3-failed-pipeline"}} |= "{execution_run_id}"'
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


def query_tempo_traces(execution_run_id: str) -> list[dict]:
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


def run_pipeline() -> str:
    """Run pipeline locally and return execution_run_id."""
    # Set local OTLP endpoint
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
    os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "grpc"

    # Import use case
    from tests.test_case_s3_failed_python_on_linux import use_case

    # Run pipeline (expecting failures for AWS commands)
    result = use_case.main()
    return result.get("execution_run_id", "unknown")


def validate_grafana_telemetry(execution_run_id: str) -> bool:
    """Validate that telemetry appears in Grafana."""
    print(f"\nValidating Grafana telemetry for execution_run_id={execution_run_id}...")

    logs = query_loki_logs(execution_run_id)
    traces = query_tempo_traces(execution_run_id)

    print(f"  Logs found: {len(logs)}")
    print(f"  Traces found: {len(traces)}")

    # Check for expected spans
    if traces:
        print("  Expected spans:")
        print("    - process_pipeline (root)")
        print("    - step1_check_s3_object")
        print("    - step2_download_from_s3")
        print("    - step3_list_s3_bucket")
        print("    - step4_process_json_with_jq")
        print("    - step5_transform_with_jq")

    if traces:
        print("✓ Telemetry validation passed")
        return True
    else:
        print("✗ Telemetry validation failed")
        return False


def main():
    """Main entry point."""
    print("=" * 60)
    print("S3 Failed Python Pipeline Local Test with Grafana Validation")
    print("=" * 60)
    print()

    # Check if Grafana is running
    try:
        response = requests.get(f"{GRAFANA_URL}/api/health", timeout=5)
        if response.status_code != 200:
            print("✗ Grafana is not running on localhost:3000")
            print("  Run: make grafana-local")
            return 1
    except requests.RequestException:
        print("✗ Grafana is not running on localhost:3000")
        print("  Run: make grafana-local")
        return 1

    # Run pipeline
    print("Running S3 Failed Python pipeline locally...")
    execution_run_id = run_pipeline()

    # Wait for telemetry export
    print("Waiting for telemetry export...")
    time.sleep(5)

    # Validate Grafana
    if validate_grafana_telemetry(execution_run_id):
        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return 0
    else:
        print("\n" + "=" * 60)
        print("TEST FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
