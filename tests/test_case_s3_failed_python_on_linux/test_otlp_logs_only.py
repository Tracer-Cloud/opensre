#!/usr/bin/env python3
"""Test OTLP logs specifically - verify logs reach Grafana Cloud."""

import logging
import os
import sys
import time

# Configure before imports
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "https://otlp-gateway-prod-eu-west-2.grafana.net/otlp"
os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = "Authorization=Basic ODkxMDkyOmdsY19leUp2SWpvaU1UQTROVGt5TWlJc0ltNGlPaUp2YkhSd0lpd2lheUk2SW10S05UQTFhV1ZPV0dZME1uUllRalV3TkRCcFRGRTNRaUlzSW0waU9uc2ljaUk2SW5CeWIyUXRaWFV0ZDJWemRDMHlJbjE5"
os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"

from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry import _logs

print("=== OTLP Logs Direct Test ===")

# Create resource
resource = Resource.create({
    "service.name": "test-s3-logs-only",
    "pipeline.name": "test_logs",
})

# Set up logging
logger_provider = LoggerProvider(resource=resource)

# Add OTLP log exporter
log_exporter = OTLPLogExporter()
logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))

# Set global logger provider
_logs.set_logger_provider(logger_provider)

# Add handler to root logger
handler = LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
root_logger = logging.getLogger()
root_logger.addHandler(handler)
root_logger.setLevel(logging.INFO)

print("✓ OTLP Logging configured")

# Emit test logs
execution_run_id = f"logs-test-{int(time.time())}"

logger = logging.getLogger(__name__)
logger.info(f"Test log 1 - execution_run_id={execution_run_id}")
logger.info(f"Test log 2 - Grafana Cloud validation")
logger.warning(f"Test warning - execution_run_id={execution_run_id}")
logger.error(f"Test error - execution_run_id={execution_run_id}")

print(f"✓ Emitted 4 log messages with execution_run_id={execution_run_id}")

# Force flush
print("Flushing log exporter...")
logger_provider.force_flush(timeout_millis=5000)

print("✓ Flush complete")
print()
print("=" * 60)
print(f"Execution Run ID: {execution_run_id}")
print()
print("Check Grafana Cloud Loki for logs:")
print(f'  Query: {{service_name="test-s3-logs-only"}} |= "{execution_run_id}"')
print()
print("Wait 30-60 seconds for ingestion.")
print("=" * 60)
