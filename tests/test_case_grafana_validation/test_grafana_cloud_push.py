import json
import logging
import os
import uuid

import pytest
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import TracerProvider

pytestmark = pytest.mark.skip(reason="outbound telemetry has been removed from this project")
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
