#!/usr/bin/env python3
"""Direct OTLP test - bypasses tracer_telemetry wrapper to debug log export."""

import json
import logging
import os
import sys
import time

# Configure OTEL before any imports
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "https://otlp-gateway-prod-eu-west-2.grafana.net/otlp"
os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = "Authorization=Basic ODkxMDkyOmdsY19leUp2SWpvaU1UQTROVGt5TWlJc0ltNGlPaUp2YkhSd0lpd2lheUk2SW10S05UQTFhV1ZPV0dZME1uUllRalV3TkRCcFRGRTNRaUlzSW0waU9uc2ljaUk2SW5CeWIyUXRaWFV0ZDJWemRDMHlJbjE5"
os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

print("=== OTLP Direct Test ===")
print(f"Endpoint: {os.environ['OTEL_EXPORTER_OTLP_ENDPOINT']}")
print(f"Protocol: {os.environ['OTEL_EXPORTER_OTLP_PROTOCOL']}")
print()

# Create resource
resource = Resource.create({
    "service.name": "test-s3-failed-direct",
    "pipeline.name": "test_direct",
    "pipeline.framework": "python",
})

# Set up tracing with console exporter for debugging
provider = TracerProvider(resource=resource)

# Add both OTLP and Console exporters
otlp_exporter = OTLPSpanExporter()
console_exporter = ConsoleSpanExporter()

provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
provider.add_span_processor(BatchSpanProcessor(console_exporter))

trace.set_tracer_provider(provider)
tracer = trace.get_tracer(__name__)

# Create test span
execution_run_id = f"direct-test-{int(time.time())}"

print(f"Creating span with execution.run_id={execution_run_id}")

with tracer.start_as_current_span("test_span") as span:
    span.set_attribute("execution.run_id", execution_run_id)
    span.set_attribute("test.type", "otlp_direct")
    span.set_attribute("log.test", "validating_grafana_cloud")
    
    print("✓ Span created with attributes")
    
    # Add events to span (these appear as span events, not logs)
    span.add_event("test_event", {
        "message": "Testing OTLP export to Grafana Cloud",
        "execution_run_id": execution_run_id,
    })
    
    time.sleep(0.5)

print("\n✓ Span closed")
print("Flushing...")

# Force flush
provider.force_flush(timeout_millis=5000)

print("✓ Flush complete")
print()
print("=" * 60)
print("Check Grafana Cloud Tempo for trace:")
print(f"  Service: test-s3-failed-direct")
print(f"  execution.run_id: {execution_run_id}")
print()
print("NOTE: OpenTelemetry logs via OTLP require explicit LoggingHandler")
print("This test only sends TRACES. To send LOGS, we need LogEmitter.")
print("=" * 60)
