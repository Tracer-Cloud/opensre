#!/usr/bin/env python3
"""Minimal test to validate logs reach Grafana Cloud via OTLP.

This script:
1. Configures OpenTelemetry to export logs via OTLP to Grafana Cloud
2. Emits test log messages
3. Creates spans with execution.run_id
4. Flushes telemetry
5. Reports success/failure
"""

import logging
import os
import sys
import time
from pathlib import Path

# Set Grafana Cloud OTLP endpoint BEFORE importing OpenTelemetry
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "https://otlp-gateway-prod-eu-west-2.grafana.net/otlp"
os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = "Authorization=Basic ODkxMDkyOmdsY19leUp2SWpvaU1UQTROVGt5TWlJc0ltNGlPaUp2YkhSd0lpd2lheUk2SW10S05UQTFhV1ZPV0dZME1uUllRalV3TkRCcFRGRTNRaUlzSW0waU9uc2ljaUk2SW5CeWIyUXRaWFV0ZDJWemRDMHlJbjE5"
os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"
os.environ["OTEL_SERVICE_NAME"] = "test-s3-failed-python"
os.environ["OTEL_RESOURCE_ATTRIBUTES"] = "pipeline.name=test_s3_failed_python,pipeline.framework=python,test_case=test_case_s3_failed_python_on_linux"

# Add shared telemetry to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "shared" / "telemetry"))

try:
    from tracer_telemetry import init_telemetry
    
    print("=== Testing OTLP Logs to Grafana Cloud ===")
    print(f"Endpoint: {os.environ['OTEL_EXPORTER_OTLP_ENDPOINT']}")
    print(f"Protocol: {os.environ['OTEL_EXPORTER_OTLP_PROTOCOL']}")
    print(f"Service: {os.environ['OTEL_SERVICE_NAME']}")
    print()
    
    # Initialize telemetry
    telemetry = init_telemetry(
        service_name="test-s3-failed-python",
        resource_attributes={
            "pipeline.name": "test_s3_failed_python",
            "pipeline.framework": "python",
        },
    )
    
    print("✓ Telemetry initialized")
    
    # Create a span with execution.run_id
    execution_run_id = f"test-{int(time.time())}"
    logger = logging.getLogger(__name__)
    
    with telemetry.tracer.start_as_current_span("test_pipeline") as span:
        span.set_attribute("execution.run_id", execution_run_id)
        span.set_attribute("test.name", "grafana_cloud_logs_test")
        
        print(f"✓ Created span with execution.run_id={execution_run_id}")
        
        # Emit test logs with structured JSON
        import json
        logger.info(json.dumps({
            "event": "test_log_message",
            "execution_run_id": execution_run_id,
            "message": "Testing OTLP logs to Grafana Cloud",
            "timestamp": time.time(),
        }))
        
        logger.info("Simple log message for Grafana Cloud")
        logger.warning(json.dumps({
            "event": "test_warning",
            "execution_run_id": execution_run_id,
            "level": "warning",
        }))
        
        print("✓ Emitted 3 log messages")
        
        # Record metrics
        telemetry.record_run(
            status="success",
            duration_seconds=1.0,
            record_count=1,
            attributes={"pipeline.name": "test_s3_failed_python"},
        )
        
        print("✓ Recorded metrics")
    
    # Force flush to ensure data is sent
    print("\nFlushing telemetry...")
    telemetry.flush()
    
    # Give exporters time to complete
    time.sleep(3)
    
    print("\n" + "=" * 60)
    print("✓ TEST COMPLETE")
    print("=" * 60)
    print(f"\nExecution Run ID: {execution_run_id}")
    print("\nCheck Grafana Cloud Loki for logs:")
    print(f'  Query: {{service_name="test-s3-failed-python"}} |= "{execution_run_id}"')
    print("\nGive it 30-60 seconds for ingestion, then refresh Grafana.")
    print("=" * 60)
    
    sys.exit(0)

except Exception as e:
    print(f"\n✗ TEST FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
