import json
import logging
import os
import uuid

import pytest
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

from app.outbound_telemetry import init_telemetry, traced_operation
from config.grafana_config import (
    configure_grafana_cloud,
    get_otlp_auth_header,
    get_otlp_endpoint,
    load_env,
)
from tests.test_case_grafana_validation.env_requirements import require_grafana_cloud_env


def _assert_force_flush(provider, *, name: str) -> None:
    if provider is None or not hasattr(provider, "force_flush"):
        pytest.fail(f"{name} provider is not configured for OTLP export")
    result = provider.force_flush(timeout_millis=5000)
    if result not in (None, True):
        pytest.fail(f"{name} force_flush returned unexpected result: {result}")


def _configure_grafana_otlp() -> None:
    load_env()
    require_grafana_cloud_env()
    endpoint = get_otlp_endpoint()
    auth_header = get_otlp_auth_header()
    configure_grafana_cloud()
    assert os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") == endpoint
    assert os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL") == "http/protobuf"
    assert os.getenv("OTEL_EXPORTER_OTLP_HEADERS") == f"Authorization={auth_header}"


@pytest.fixture(scope="session")
def telemetry():
    _configure_grafana_otlp()
    service_name = f"grafana-push-test-{uuid.uuid4().hex[:8]}"
    return init_telemetry(
        service_name=service_name,
        resource_attributes={"test.suite": "grafana_cloud_push"},
    )


def test_grafana_cloud_push_metrics(telemetry):
    run_id = uuid.uuid4().hex
    attributes = {"test.run_id": run_id, "test.signal": "metrics"}
    telemetry.metrics.runs_total.add(1, attributes)
    telemetry.metrics.duration_seconds.record(0.05, attributes)
    telemetry.flush()

    provider = otel_metrics.get_meter_provider()
    assert isinstance(provider, MeterProvider)
    _assert_force_flush(provider, name="metrics")


def test_grafana_cloud_push_traces(telemetry):
    run_id = uuid.uuid4().hex
    with traced_operation(
        telemetry.tracer,
        "grafana_push_test_span",
        {"test.run_id": run_id, "test.signal": "traces"},
    ):
        pass
    telemetry.flush()

    provider = otel_trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    _assert_force_flush(provider, name="traces")


def test_grafana_cloud_push_logs(telemetry):
    run_id = uuid.uuid4().hex
    logger = logging.getLogger("grafana_push_test")
    logger.info(
        json.dumps({"event": "grafana_push_test", "test.run_id": run_id, "test.signal": "logs"})
    )
    telemetry.flush()

    try:
        from opentelemetry import _logs as otel_logs
        from opentelemetry.sdk._logs import LoggerProvider
    except ImportError:
        pytest.fail("OpenTelemetry logs SDK is not installed")

    provider = otel_logs.get_logger_provider()
    assert isinstance(provider, LoggerProvider)
    _assert_force_flush(provider, name="logs")
