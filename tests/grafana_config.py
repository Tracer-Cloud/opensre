from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from opentelemetry.sdk.resources import Resource

DEFAULT_INSTANCE_URL = "https://tracerbio.grafana.net"
DEFAULT_LOKI_UID = "grafanacloud-logs"
DEFAULT_TEMPO_UID = "grafanacloud-traces"
DEFAULT_MIMIR_UID = "grafanacloud-prom"


def _get_env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def get_env(key: str, default: str = "") -> str:
    load_env()
    return _get_env(key, default)


def load_env(env_path: Path | str | None = None, *, override: bool = False) -> None:
    if os.getenv("GRAFANA_CONFIG_SKIP_ENV_FILE") == "1":
        return
    if env_path is None:
        env_path = Path.cwd() / ".env"
    path = Path(env_path)
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and (override or key not in os.environ):
            os.environ[key] = value


def get_account_read_token(account_id: str) -> str:
    if account_id == "tracerbio":
        return get_grafana_read_token()
    return get_env(f"GRAFANA_{account_id.upper()}_READ_TOKEN", "")


def get_account_instance_url(account_id: str) -> str:
    if account_id == "tracerbio":
        return get_grafana_instance_url()
    return get_env(f"GRAFANA_{account_id.upper()}_INSTANCE_URL", "")


def get_account_datasource_uids(account_id: str) -> tuple[str, str, str]:
    load_env()
    if account_id == "tracerbio":
        return get_datasource_uids()
    prefix = f"GRAFANA_{account_id.upper()}"
    loki_uid = _get_env(f"{prefix}_LOKI_DATASOURCE_UID", DEFAULT_LOKI_UID)
    tempo_uid = _get_env(f"{prefix}_TEMPO_DATASOURCE_UID", DEFAULT_TEMPO_UID)
    mimir_uid = _get_env(f"{prefix}_MIMIR_DATASOURCE_UID", DEFAULT_MIMIR_UID)
    return loki_uid, tempo_uid, mimir_uid


def list_account_ids() -> list[str]:
    load_env()
    accounts = {"tracerbio"}
    for key in os.environ:
        if key.startswith("GRAFANA_") and key.endswith("_READ_TOKEN"):
            account_id = key[len("GRAFANA_") : -len("_READ_TOKEN")].lower()
            if account_id:
                accounts.add(account_id)
    return sorted(accounts)


def get_grafana_read_token() -> str:
    return get_env("GRAFANA_READ_TOKEN", "")


def get_grafana_instance_url() -> str:
    return get_env("GRAFANA_INSTANCE_URL", DEFAULT_INSTANCE_URL)


def get_datasource_uids() -> tuple[str, str, str]:
    load_env()
    loki_uid = _get_env("GRAFANA_LOKI_DATASOURCE_UID", DEFAULT_LOKI_UID)
    tempo_uid = _get_env("GRAFANA_TEMPO_DATASOURCE_UID", DEFAULT_TEMPO_UID)
    mimir_uid = _get_env("GRAFANA_MIMIR_DATASOURCE_UID", DEFAULT_MIMIR_UID)
    return loki_uid, tempo_uid, mimir_uid


def get_otlp_endpoint() -> str:
    return get_env("GCLOUD_OTLP_ENDPOINT", "")


def get_otlp_auth_header() -> str:
    return get_env("GCLOUD_OTLP_AUTH_HEADER", "")


def get_otel_protocol() -> str:
    return get_env("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")


def get_effective_otlp_endpoint() -> str:
    load_env()
    return _get_env("OTEL_EXPORTER_OTLP_ENDPOINT") or _get_env("GCLOUD_OTLP_ENDPOINT", "")


def get_otel_exporter_otlp_protocol(default: str = "grpc") -> str:
    return get_env("OTEL_EXPORTER_OTLP_PROTOCOL", default)


def get_otel_exporter_otlp_metrics_protocol(default: str = "grpc") -> str:
    load_env()
    return _get_env(
        "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
        _get_env("OTEL_EXPORTER_OTLP_PROTOCOL", default),
    )


def get_otel_exporter_otlp_endpoint(default: str = "") -> str:
    return get_env("OTEL_EXPORTER_OTLP_ENDPOINT", default)


def get_otel_exporter_otlp_metrics_endpoint(default: str = "") -> str:
    return get_env("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", default)


def get_otel_exporter_otlp_headers(default: str = "") -> str:
    return get_env("OTEL_EXPORTER_OTLP_HEADERS", default)


def get_aws_lambda_function_name(default: str = "") -> str:
    return get_env("AWS_LAMBDA_FUNCTION_NAME", default)


def parse_otel_headers(headers_str: str | None = None) -> dict[str, str]:
    headers_raw = headers_str if headers_str is not None else get_otel_exporter_otlp_headers()
    headers: dict[str, str] = {}
    if headers_raw:
        for pair in headers_raw.split(","):
            if "=" in pair:
                key, value = pair.split("=", 1)
                headers[key.strip()] = value.strip()
    return headers


def get_hosted_logs_id() -> str:
    return get_env("GCLOUD_HOSTED_LOGS_ID", "")


def get_hosted_logs_url() -> str:
    return get_env("GCLOUD_HOSTED_LOGS_URL", "")


def get_hosted_metrics_id() -> str:
    return get_env("GCLOUD_HOSTED_METRICS_ID", "")


def get_hosted_metrics_url() -> str:
    return get_env("GCLOUD_HOSTED_METRICS_URL", "")


def get_hosted_traces_id() -> str:
    return get_env("GCLOUD_HOSTED_TRACES_ID", "")


def get_hosted_traces_url() -> str:
    load_env()
    traces_url = _get_env("GCLOUD_HOSTED_TRACES_URL_TEMPO") or _get_env(
        "GCLOUD_HOSTED_TRACES_URL", ""
    )
    return traces_url


def get_rw_api_key() -> str:
    return get_env("GCLOUD_RW_API_KEY", "")


def _is_grafana_hostname(endpoint: str) -> bool:
    hostname = urlparse(endpoint).hostname or ""
    return hostname == "grafana.net" or hostname.endswith(".grafana.net") or hostname == "grafana.com" or hostname.endswith(".grafana.com")


def is_grafana_otlp_endpoint(value: str | None = None) -> bool:
    endpoint = value if value is not None else get_effective_otlp_endpoint()
    return _is_grafana_hostname(endpoint)


def configure_grafana_cloud(env_file: Path | str | None = None) -> None:
    """
    Configure OTLP to send telemetry to Grafana Cloud.

    Args:
        env_file: Optional path to .env file containing Grafana Cloud credentials.
                  If provided, loads environment variables from this file.

    Raises:
        ValueError: If GCLOUD_OTLP_ENDPOINT is not set after loading .env.

    Environment variables used:
        - GCLOUD_OTLP_ENDPOINT: Grafana Cloud OTLP endpoint (required)
        - GCLOUD_OTLP_AUTH_HEADER: Authorization header value (optional but recommended)
    """
    load_env(env_file)

    endpoint = get_otlp_endpoint()
    auth_header = get_otlp_auth_header()

    if not endpoint:
        raise ValueError("GCLOUD_OTLP_ENDPOINT not set in environment")

    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = endpoint
    os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"
    os.environ["OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE"] = "cumulative"
    if auth_header:
        os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization={auth_header}"


def apply_otel_env_defaults() -> None:
    """Apply OpenTelemetry environment defaults, preferring Grafana Cloud config if available."""
    load_env()
    gcloud_endpoint = get_otlp_endpoint()

    if not get_otel_exporter_otlp_endpoint() and gcloud_endpoint:
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = gcloud_endpoint

    gcloud_auth = get_otlp_auth_header()
    if not get_otel_exporter_otlp_headers() and gcloud_auth:
        os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization={gcloud_auth}"

    if not get_otel_exporter_otlp_protocol(default="") and gcloud_endpoint:
        os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"


def validate_grafana_cloud_config() -> bool:
    """Validate that Grafana Cloud configuration is present when using cloud endpoints."""
    endpoint = get_effective_otlp_endpoint()
    if _is_grafana_hostname(endpoint):
        required_values = {
            "GCLOUD_HOSTED_METRICS_ID": get_hosted_metrics_id(),
            "GCLOUD_HOSTED_METRICS_URL": get_hosted_metrics_url(),
            "GCLOUD_HOSTED_LOGS_ID": get_hosted_logs_id(),
            "GCLOUD_HOSTED_LOGS_URL": get_hosted_logs_url(),
            "GCLOUD_RW_API_KEY": get_rw_api_key(),
            "GCLOUD_OTLP_ENDPOINT": get_otlp_endpoint(),
            "GCLOUD_OTLP_AUTH_HEADER": get_otlp_auth_header(),
        }
        missing = [key for key, value in required_values.items() if not value]
        if missing:
            raise ValueError(
                f"Grafana Cloud endpoint detected but missing env vars: {', '.join(missing)}"
            )
    return True


def build_resource(service_name: str, extra_attributes: dict[str, Any] | None) -> Resource:
    attributes: dict[str, Any] = {"service.name": service_name}
    if extra_attributes:
        attributes.update(extra_attributes)
    return Resource.create(attributes)
