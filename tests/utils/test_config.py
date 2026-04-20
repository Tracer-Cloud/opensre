import os
from contextlib import contextmanager

import pytest

from app.utils.config import (
    apply_otel_env_defaults,
    configure_grafana_cloud,
    validate_grafana_cloud_config,
)


@contextmanager
def temp_env(values: dict[str, str]):
    original = os.environ.copy()
    keys_to_clear = [
        "GCLOUD_HOSTED_METRICS_ID",
        "GCLOUD_HOSTED_METRICS_URL",
        "GCLOUD_HOSTED_LOGS_ID",
        "GCLOUD_HOSTED_LOGS_URL",
        "GCLOUD_RW_API_KEY",
        "GCLOUD_OTLP_ENDPOINT",
        "GCLOUD_OTLP_AUTH_HEADER",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
    ]
    for key in keys_to_clear:
        os.environ.pop(key, None)
    os.environ.update({"GRAFANA_CONFIG_SKIP_ENV_FILE": "1", **values})
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def test_configure_grafana_cloud_sets_otel_vars():
    with temp_env(
        {
            "GCLOUD_OTLP_ENDPOINT": "https://example.grafana.net/otlp",
            "GCLOUD_OTLP_AUTH_HEADER": "Basic abc123",
        }
    ):
        configure_grafana_cloud()
        assert os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") == "https://example.grafana.net/otlp"
        assert os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL") == "http/protobuf"
        assert os.getenv("OTEL_EXPORTER_OTLP_HEADERS") == "Authorization=Basic abc123"


def test_apply_otel_env_defaults_sets_when_missing():
    with temp_env(
        {
            "GCLOUD_OTLP_ENDPOINT": "https://example.grafana.net/otlp",
            "GCLOUD_OTLP_AUTH_HEADER": "Basic abc123",
        }
    ):
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
        os.environ.pop("OTEL_EXPORTER_OTLP_HEADERS", None)
        os.environ.pop("OTEL_EXPORTER_OTLP_PROTOCOL", None)
        apply_otel_env_defaults()
        assert os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") == "https://example.grafana.net/otlp"
        assert os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL") == "http/protobuf"
        assert os.getenv("OTEL_EXPORTER_OTLP_HEADERS") == "Authorization=Basic abc123"


def test_validate_grafana_cloud_config_flags_missing():
    with (
        temp_env(
            {
                "GCLOUD_OTLP_ENDPOINT": "https://example.grafana.net/otlp",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://example.grafana.net/otlp",
            }
        ),
        pytest.raises(ValueError, match="missing env vars"),
    ):
        validate_grafana_cloud_config()


def test_validate_grafana_cloud_config_passes_when_present():
    with temp_env(
        {
            "OTEL_EXPORTER_OTLP_ENDPOINT": "https://example.grafana.net/otlp",
            "GCLOUD_HOSTED_METRICS_ID": "1",
            "GCLOUD_HOSTED_METRICS_URL": "https://metrics",
            "GCLOUD_HOSTED_LOGS_ID": "2",
            "GCLOUD_HOSTED_LOGS_URL": "https://logs",
            "GCLOUD_RW_API_KEY": "token",
            "GCLOUD_OTLP_ENDPOINT": "https://example.grafana.net/otlp",
            "GCLOUD_OTLP_AUTH_HEADER": "Basic abc123",
        }
    ):
        assert validate_grafana_cloud_config() is True
