from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# Try to import gRPC exporter first, fall back to HTTP if not available
try:
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
except ImportError:
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except ImportError:
        OTLPSpanExporter = None


def _get_execution_run_id_from_context() -> str | None:
    """Extract execution.run_id from the current active span context."""
    span = trace.get_current_span()
    if span and span.is_recording():
        attrs = span.get_attributes() or {}
        return attrs.get("execution.run_id")
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
    if OTLPSpanExporter is not None:
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    return trace.get_tracer("tracer_telemetry")
