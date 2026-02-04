from __future__ import annotations

import os
from typing import Any

from opentelemetry.sdk.resources import Resource

from config.grafana_config import (
    get_hosted_logs_id,
    get_hosted_logs_url,
    get_hosted_metrics_id,
    get_hosted_metrics_url,
    get_otlp_auth_header,
    get_otlp_endpoint,
    get_rw_api_key,
    is_grafana_otlp_endpoint,
)


def apply_otel_env_defaults() -> None:
    """Apply OpenTelemetry environment defaults, preferring Grafana Cloud config if available."""
    gcloud_endpoint = get_otlp_endpoint()
    is_grafana_cloud = bool(gcloud_endpoint and is_grafana_otlp_endpoint(gcloud_endpoint))

    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") and gcloud_endpoint:
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = gcloud_endpoint

    gcloud_auth = get_otlp_auth_header()
    if not os.getenv("OTEL_EXPORTER_OTLP_HEADERS") and gcloud_auth:
        os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization={gcloud_auth}"

    if not os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL"):
        # Use HTTP for Grafana Cloud (gRPC has ALPN issues), gRPC for local collectors
        os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf" if is_grafana_cloud else "grpc"


def validate_grafana_cloud_config() -> bool:
    """Validate that Grafana Cloud configuration is present when using cloud endpoints."""
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if is_grafana_otlp_endpoint(endpoint):
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
