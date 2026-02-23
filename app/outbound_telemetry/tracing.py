from __future__ import annotations

import json
import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

from config.grafana_config import (
    get_effective_otlp_endpoint,
    get_otel_exporter_otlp_endpoint,
    get_otel_exporter_otlp_protocol,
)


def _get_span_exporter():
    """Get the appropriate span exporter based on OTEL_EXPORTER_OTLP_PROTOCOL."""
    protocol = get_otel_exporter_otlp_protocol()

    if protocol in ("http/protobuf", "http"):
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            return OTLPSpanExporter()
        except ImportError:
            pass

    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore[import-untyped]
            OTLPSpanExporter,
        )
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
                    "protocol": get_otel_exporter_otlp_protocol(),
                    "exporter": exporter.__class__.__name__,
                    "endpoint": get_otel_exporter_otlp_endpoint(),
                }
            )
        )
    elif get_effective_otlp_endpoint():
        logging.getLogger(__name__).warning("OTLP trace exporter is unavailable")
    trace.set_tracer_provider(provider)
    return trace.get_tracer("outbound_telemetry")
