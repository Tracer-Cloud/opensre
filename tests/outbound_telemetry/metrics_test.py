import os
from contextlib import contextmanager

from opentelemetry.sdk.resources import Resource

from app.outbound_telemetry.metrics import PipelineMetrics, setup_metrics


@contextmanager
def temp_env(values: dict[str, str]):
    original = os.environ.copy()
    os.environ.update(values)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def test_setup_metrics_returns_pipeline_metrics():
    with temp_env(
        {
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318",
        }
    ):
        metrics = setup_metrics(Resource.create({}))
        assert isinstance(metrics, PipelineMetrics)
        assert metrics.runs_total is not None
