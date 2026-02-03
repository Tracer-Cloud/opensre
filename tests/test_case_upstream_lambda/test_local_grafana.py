#!/usr/bin/env python3
"""
Local test for Lambda handler with Grafana validation.

Runs Lambda handler locally with local OTLP endpoint and validates
logs and traces appear in Grafana.
"""

import os
import sys
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from tests.shared.grafana_validation import GrafanaValidator

SERVICE_NAME = "lambda-mock-dag"
validator = GrafanaValidator(service_name=SERVICE_NAME)


def run_lambda_handler() -> str:
    """Run Lambda handler locally and return execution_run_id."""
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
    os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "grpc"

    sys.path.insert(0, str(Path(__file__).parent / "pipeline_code" / "mock_dag"))
    from handler import lambda_handler

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

    try:
        result = lambda_handler(event, None)
        return result.get("correlation_id", "unknown")
    except Exception as e:
        print(f"Lambda handler failed: {e}")
        return "unknown"


def main():
    """Main entry point."""
    print("=" * 60)
    print("Lambda Handler Local Test with Grafana Validation")
    print("=" * 60)
    print()

    print("Running Lambda handler locally...")
    execution_run_id = run_lambda_handler()

    print("Waiting for telemetry export...")
    time.sleep(5)

    if validator.validate(execution_run_id):
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
