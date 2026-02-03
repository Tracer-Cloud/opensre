from __future__ import annotations

import json
import logging
import os

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def _get_span_exporter():
    """Get the appropriate span exporter based on OTEL_EXPORTER_OTLP_PROTOCOL."""
    protocol = os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")

    # Use HTTP for http/protobuf protocol (required for Grafana Cloud)
    if protocol in ("http/protobuf", "http"):
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            return OTLPSpanExporter()
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
        logging.getLogger(__name__).info(
            json.dumps(
                {
                    "event": "otel_tracing_configured",
                    "protocol": os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc"),
                    "exporter": exporter.__class__.__name__,
                    "endpoint": os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
                }
            )
        )
    else:
        logging.getLogger(__name__).warning("OTLP trace exporter is unavailable")
    trace.set_tracer_provider(provider)
    return trace.get_tracer("tracer_telemetry")
