from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from opentelemetry.sdk.resources import Resource

from config.grafana_config import (
    get_effective_otlp_endpoint,
    get_hosted_logs_id,
    get_hosted_logs_url,
    get_hosted_metrics_id,
    get_hosted_metrics_url,
    get_otlp_auth_header,
    get_otlp_endpoint,
    get_rw_api_key,
    load_env,
)


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
    if auth_header:
        os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization={auth_header}"


def apply_otel_env_defaults() -> None:
    """Apply OpenTelemetry environment defaults, preferring Grafana Cloud config if available."""
    load_env()
    gcloud_endpoint = get_otlp_endpoint()

    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") and gcloud_endpoint:
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = gcloud_endpoint

    gcloud_auth = get_otlp_auth_header()
    if not os.getenv("OTEL_EXPORTER_OTLP_HEADERS") and gcloud_auth:
        os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization={gcloud_auth}"

    if not os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL") and gcloud_endpoint:
        os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"


def validate_grafana_cloud_config() -> bool:
    """Validate that Grafana Cloud configuration is present when using cloud endpoints."""
    endpoint = get_effective_otlp_endpoint()
    if "grafana.net" in endpoint or "grafana.com" in endpoint:
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
            import warnings

            warnings.warn(
                f"Grafana Cloud endpoint detected but missing env vars: {', '.join(missing)}",
                UserWarning,
                stacklevel=2,
            )
            return False
    return True


def build_resource(service_name: str, extra_attributes: dict[str, Any] | None) -> Resource:
    attributes: dict[str, Any] = {"service.name": service_name}
    if extra_attributes:
        attributes.update(extra_attributes)
    return Resource.create(attributes)
