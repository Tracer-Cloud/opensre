#!/usr/bin/env python3
"""
Local test for Lambda handler with Grafana validation.

Runs Lambda handler locally with local OTLP endpoint and validates
logs and traces appear in Grafana.
"""

import json
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
        query = f'{{service_name="lambda-mock-dag"}} |= "{execution_run_id}"'
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


def run_lambda_handler() -> str:
    """Run Lambda handler locally and return execution_run_id."""
    # Set local OTLP endpoint
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
    os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "grpc"
    
    # Import handler
    sys.path.insert(0, str(Path(__file__).parent / "pipeline_code" / "mock_dag"))
    from handler import lambda_handler
    
    # Create test event
    event = {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "test-bucket"},
                    "object": {"key": "test-key"},
                }
            }
        ]
    }
    
    # Run handler
    try:
        result = lambda_handler(event, None)
        correlation_id = result.get("correlation_id", "unknown")
        return correlation_id
    except Exception as e:
        print(f"Lambda handler failed: {e}")
        return "unknown"


def validate_grafana_telemetry(execution_run_id: str) -> bool:
    """Validate that telemetry appears in Grafana."""
    print(f"\nValidating Grafana telemetry for execution_run_id={execution_run_id}...")
    
    logs = query_loki_logs(execution_run_id)
    traces = query_tempo_traces(execution_run_id)
    
    print(f"  Logs found: {len(logs)}")
    print(f"  Traces found: {len(traces)}")
    
    if logs and traces:
        print("✓ Telemetry validation passed")
        return True
    else:
        print("✗ Telemetry validation failed")
        return False


def main():
    """Main entry point."""
    print("=" * 60)
    print("Lambda Handler Local Test with Grafana Validation")
    print("=" * 60)
    print()
    
    # Run Lambda handler
    print("Running Lambda handler locally...")
    execution_run_id = run_lambda_handler()
    
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
