from opentelemetry.sdk.resources import Resource

from app.outbound_telemetry.tracing import setup_tracing, traced_operation
from tests.outbound_telemetry.conftest import temp_env


def test_setup_tracing_and_traced_operation():
    with temp_env(
        {
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
        }
    ):
        tracer = setup_tracing(Resource.create({}))
        with traced_operation(tracer, "outbound.test", {"key": "value"}) as span:
            span.set_attribute("result.count", 1)
