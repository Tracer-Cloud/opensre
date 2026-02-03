#!/usr/bin/env python3
"""
Local test for CloudWatch Demo with Grafana validation.

Runs pipeline locally with local OTLP endpoint and validates
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

SERVICE_NAME = "cloudwatch-demo"
validator = GrafanaValidator(service_name=SERVICE_NAME, require_logs=False)


def run_pipeline() -> str:
    """Run pipeline locally and return execution_run_id."""
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
    os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "grpc"

    from tests.test_case_cloudwatch_demo import use_case

    try:
        result = use_case.main()
        return result.get("execution_run_id", "unknown")
    except (FileNotFoundError, ValueError) as e:
        print(f"Pipeline failed as expected: {e}")
        return "test-failed"


def main():
    """Main entry point."""
    print("=" * 60)
    print("CloudWatch Demo Local Test with Grafana Validation")
    print("=" * 60)
    print()

    if not validator.require_grafana_running():
        return 1

    print("Running CloudWatch Demo pipeline locally...")
    execution_run_id = run_pipeline()

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
