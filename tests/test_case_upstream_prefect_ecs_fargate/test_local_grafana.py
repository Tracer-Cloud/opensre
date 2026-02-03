#!/usr/bin/env python3
"""
Local test for Prefect flow with Grafana validation.

Extends test_local.py to validate Grafana after flow execution:
- Query Loki for structured logs
- Query Tempo for trace spans
"""

import json
import os
import sys
import time
from pathlib import Path

import requests

# Import from test_local.py
sys.path.insert(0, str(Path(__file__).parent))
from test_local import main as test_local_main, run_flow, write_test_data, verify_output

# Local Grafana endpoints
GRAFANA_URL = "http://localhost:3000"
LOKI_URL = f"{GRAFANA_URL}/loki/api/v1"
TEMPO_URL = f"{GRAFANA_URL}/api/tempo/api/traces"


def query_loki_logs(execution_run_id: str) -> list[dict]:
    """Query Loki for logs containing execution_run_id."""
    try:
        query = f'{{service_name="prefect-etl-pipeline"}} |= "{execution_run_id}"'
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
    
    if logs and traces:
        print("✓ Telemetry validation passed")
        return True
    else:
        print("✗ Telemetry validation failed")
        return False


def main():
    """Main entry point with Grafana validation."""
    # Set local OTLP endpoint
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
    os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "grpc"
    
    # Run the flow
    from test_local import LANDING_BUCKET
    
    s3_key = write_test_data(inject_error=False)
    result = run_flow(LANDING_BUCKET, s3_key)
    
    # Extract execution_run_id from result
    execution_run_id = result.get("correlation_id", "unknown")
    
    # Wait for telemetry export
    print("Waiting for telemetry export...")
    time.sleep(5)
    
    # Validate Grafana
    grafana_valid = validate_grafana_telemetry(execution_run_id)
    
    # Verify output
    output_valid = verify_output(s3_key)
    
    if grafana_valid and output_valid:
        print("\n" + "=" * 60)
        print("TEST PASSED (with Grafana validation)")
        print("=" * 60)
        return 0
    else:
        print("\n" + "=" * 60)
        print("TEST FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
