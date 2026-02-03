#!/usr/bin/env python3
"""
Test local telemetry export to Grafana Cloud.

This script runs the Flink job logic locally (without Docker/ECS) and sends
logs, traces, and metrics directly to Grafana Cloud via OTLP.

Requirements:
- Set GCLOUD_* environment variables in .env file
- Run with: export $(grep -v '^#' .env | xargs) && python3 test_local_grafana_cloud.py
"""

import logging
import os
import sys
import time
import uuid
from pathlib import Path

# Add telemetry module to path
telemetry_path = Path(__file__).resolve().parents[1] / "shared" / "telemetry"
if telemetry_path.exists():
    sys.path.insert(0, str(telemetry_path))

from tracer_telemetry import init_telemetry

PIPELINE_NAME = "upstream_downstream_pipeline_flink"
SERVICE_NAME = "flink-etl-pipeline"


def run_test():
    """Run a test pipeline with telemetry exported to Grafana Cloud."""
    correlation_id = f"local-test-{uuid.uuid4().hex[:8]}"

    # Initialize telemetry first (this sets up OTEL env vars)
    telemetry = init_telemetry(
        service_name=SERVICE_NAME,
        resource_attributes={
            "pipeline.name": PIPELINE_NAME,
            "pipeline.framework": "flink",
            "test.type": "local_grafana_cloud",
        },
    )
    tracer = telemetry.tracer

    print(f"Starting local telemetry test")
    print(f"Correlation ID: {correlation_id}")
    print(f"OTEL Endpoint: {os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT', 'not set')}")
    print(f"OTEL Protocol: {os.getenv('OTEL_EXPORTER_OTLP_PROTOCOL', 'not set')}")
    print()

    logger = logging.getLogger("flink_test")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)

    start_time = time.monotonic()

    with tracer.start_as_current_span("test_pipeline_run") as root_span:
        root_span.set_attribute("execution.run_id", correlation_id)
        root_span.set_attribute("correlation_id", correlation_id)

        logger.info(f"[FLINK] Starting test pipeline run correlation_id={correlation_id}")

        with tracer.start_as_current_span("extract_data") as span:
            span.set_attribute("execution.run_id", correlation_id)
            span.set_attribute("record_count", 10)
            logger.info(f"[FLINK] Extracting data - 10 records")
            time.sleep(0.1)

        with tracer.start_as_current_span("validate_data") as span:
            span.set_attribute("execution.run_id", correlation_id)
            span.set_attribute("record_count", 10)
            span.set_attribute("validation_passed", True)
            logger.info(f"[FLINK] Validating data - all records passed")
            time.sleep(0.1)

        with tracer.start_as_current_span("transform_data") as span:
            span.set_attribute("execution.run_id", correlation_id)
            span.set_attribute("record_count", 10)
            span.set_attribute("features_computed", 8)
            logger.info(f"[FLINK] Transforming data - computed 8 ML features")
            time.sleep(0.1)

        with tracer.start_as_current_span("load_data") as span:
            span.set_attribute("execution.run_id", correlation_id)
            span.set_attribute("record_count", 10)
            span.set_attribute("output_location", "s3://test-bucket/output")
            logger.info(f"[FLINK] Loading data - wrote to output location")
            time.sleep(0.1)

        logger.info(f"[FLINK] Pipeline completed successfully correlation_id={correlation_id}")

    duration = time.monotonic() - start_time
    telemetry.record_run(
        status="success",
        duration_seconds=duration,
        record_count=10,
        attributes={"pipeline.name": PIPELINE_NAME},
    )

    print()
    print(f"Pipeline completed in {duration:.2f}s")
    print(f"Flushing telemetry...")
    telemetry.flush()
    time.sleep(2)

    print()
    print("=" * 60)
    print("TELEMETRY SENT TO GRAFANA CLOUD")
    print("=" * 60)
    print(f"Correlation ID: {correlation_id}")
    print(f"Service: {SERVICE_NAME}")
    print()
    print("To verify in Grafana Cloud:")
    print(f'  Logs:   {{service_name="{SERVICE_NAME}"}} |= "{correlation_id}"')
    print(f'  Traces: {{.service.name="{SERVICE_NAME}"}}')
    print()

    return correlation_id


if __name__ == "__main__":
    run_test()
