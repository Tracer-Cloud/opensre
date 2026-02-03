#!/usr/bin/env python3
"""
Local test for Airflow DAG with Grafana validation.

NOTE: This test requires Airflow to be running in Docker.
The Airflow DAG already has telemetry integrated via tracer_telemetry module.

To run locally:
1. Start local Grafana stack: make grafana-local
2. Start Airflow in Docker (see infrastructure_code/airflow_image/)
3. Configure OTLP endpoint in Airflow to point to localhost:4317
4. Trigger the DAG via Airflow UI or API
5. Run this script to validate telemetry
"""

import sys
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from tests.shared.grafana_validation import GrafanaValidator

SERVICE_NAME = "airflow-etl-pipeline"
EXPECTED_SPANS = ["extract_data", "validate_data", "transform_data", "load_data"]
validator = GrafanaValidator(service_name=SERVICE_NAME, expected_spans=EXPECTED_SPANS)


def main():
    """Main entry point."""
    print("=" * 60)
    print("Airflow DAG Local Test with Grafana Validation")
    print("=" * 60)
    print()

    if not validator.require_grafana_running():
        return 1

    print("This test validates telemetry from an Airflow DAG run.")
    print("You must first:")
    print("  1. Run Airflow locally in Docker")
    print("  2. Configure Airflow to export OTLP to localhost:4317")
    print("  3. Trigger a DAG run and note the dag_run_id")
    print()
    execution_run_id = input("Enter the dag_run_id to validate: ").strip()

    if not execution_run_id:
        print("No execution_run_id provided. Exiting.")
        return 1

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
