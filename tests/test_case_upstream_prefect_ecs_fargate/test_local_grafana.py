#!/usr/bin/env python3
"""
Local test for Prefect flow with Grafana validation.

Extends test_local.py to validate Grafana after flow execution:
- Query Loki for structured logs
- Query Tempo for trace spans
"""

import sys
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from tests.shared.grafana_validation import GrafanaValidator

# Import from test_local.py
sys.path.insert(0, str(Path(__file__).parent))
from test_local import LANDING_BUCKET, run_flow, verify_output, write_test_data

SERVICE_NAME = "prefect-etl-pipeline"
validator = GrafanaValidator(service_name=SERVICE_NAME)


def main():
    """Main entry point with Grafana validation."""
    import os

    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
    os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "grpc"

    s3_key = write_test_data(inject_error=False)
    result = run_flow(LANDING_BUCKET, s3_key)

    execution_run_id = result.get("correlation_id", "unknown")

    print("Waiting for telemetry export...")
    time.sleep(5)

    grafana_valid = validator.validate(execution_run_id)
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
