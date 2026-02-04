from __future__ import annotations

import json
import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode


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


@contextmanager
def traced_operation(
    tracer: trace.Tracer,
    name: str,
    attributes: dict[str, Any] | None = None,
) -> Generator[trace.Span, None, None]:
    """
    Context manager for creating spans with proper error recording.

    Automatically:
    - Records exceptions on the span
    - Sets error status on failure
    - Propagates context automatically via OpenTelemetry

    Usage:
        with traced_operation(tracer, "my_operation", {"key": "value"}) as span:
            # do work
            span.set_attribute("result.count", 42)
    """
    with tracer.start_as_current_span(name) as span:
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, value)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


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
