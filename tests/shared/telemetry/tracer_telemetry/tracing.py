from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def _get_span_exporter():
    """Get the appropriate span exporter based on OTEL_EXPORTER_OTLP_PROTOCOL."""
    protocol = os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    headers_str = os.getenv("OTEL_EXPORTER_OTLP_HEADERS", "")

    # Parse headers string (format: "key1=value1,key2=value2")
    headers = {}
    if headers_str:
        for pair in headers_str.split(","):
            if "=" in pair:
                key, value = pair.split("=", 1)
                headers[key.strip()] = value.strip()

    # Use HTTP for http/protobuf protocol (required for Grafana Cloud)
    if protocol in ("http/protobuf", "http"):
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            return OTLPSpanExporter(endpoint=endpoint, headers=headers) if endpoint else OTLPSpanExporter(headers=headers)
        except ImportError:
            pass

    # Fall back to gRPC
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        return OTLPSpanExporter()
    except ImportError:
        pass

    return None


def _get_execution_run_id_from_context() -> str | None:
    """Extract execution.run_id from the current active span context."""
    span = trace.get_current_span()
    if span and span.is_recording() and hasattr(span, "attributes"):
        return span.attributes.get("execution.run_id")
    return None


def ensure_execution_run_id(span: trace.Span, execution_run_id: str | None = None) -> None:
    """Ensure execution.run_id is set on a span, inheriting from context if not provided."""
    if execution_run_id:
        span.set_attribute("execution.run_id", execution_run_id)
    else:
        inherited_id = _get_execution_run_id_from_context()
        if inherited_id:
            span.set_attribute("execution.run_id", inherited_id)


def setup_tracing(resource) -> trace.Tracer:
    provider = TracerProvider(resource=resource)
    exporter = _get_span_exporter()
    if exporter is not None:
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return trace.get_tracer("tracer_telemetry")
