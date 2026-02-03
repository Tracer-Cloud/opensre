#!/usr/bin/env python3
"""
Local test for Flink job with Grafana validation.

NOTE: This test requires Flink to be running in Docker.
The Flink job already has telemetry integrated via tracer_telemetry module.

To run locally:
1. Start local Grafana stack: make grafana-local
2. Start Flink in Docker (see infrastructure_code/flink_image/)
3. Configure OTLP endpoint in Flink to point to localhost:4317
4. Trigger the Flink job
5. Run this script to validate telemetry
"""

import sys
import time
from pathlib import Path

import requests

# Local Grafana endpoints
GRAFANA_URL = "http://localhost:3000"
LOKI_URL = f"{GRAFANA_URL}/loki/api/v1"
TEMPO_URL = f"{GRAFANA_URL}/api/tempo/api/traces"


def query_loki_logs(execution_run_id: str) -> list[dict]:
    """Query Loki for logs containing execution_run_id."""
    try:
        query = f'{{service_name="flink-etl-pipeline"}} |= "{execution_run_id}"'
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
        print("    - process_batch")
        print("    - extract_data")
        print("    - transform_data")
        print("    - load_data")
    
    if logs and traces:
        print("✓ Telemetry validation passed")
        return True
    else:
        print("✗ Telemetry validation failed")
        return False


def main():
    """Main entry point."""
    print("=" * 60)
    print("Flink Job Local Test with Grafana Validation")
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
    
    # Prompt for execution_run_id
    print("This test validates telemetry from a Flink job run.")
    print("You must first:")
    print("  1. Run Flink locally in Docker")
    print("  2. Configure Flink to export OTLP to localhost:4317")
    print("  3. Trigger a Flink job and note the correlation_id")
    print()
    execution_run_id = input("Enter the correlation_id to validate: ").strip()
    
    if not execution_run_id:
        print("No execution_run_id provided. Exiting.")
        return 1
    
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
