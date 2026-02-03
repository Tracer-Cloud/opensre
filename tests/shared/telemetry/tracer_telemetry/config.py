from __future__ import annotations

import os
from typing import Any

from opentelemetry.sdk.resources import Resource


def apply_otel_env_defaults() -> None:
    """Apply OpenTelemetry environment defaults, preferring Grafana Cloud config if available."""
    gcloud_endpoint = os.getenv("GCLOUD_OTLP_ENDPOINT")
    is_grafana_cloud = gcloud_endpoint and ("grafana.net" in gcloud_endpoint or "grafana.com" in gcloud_endpoint)

    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        if gcloud_endpoint:
            os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = gcloud_endpoint

    if not os.getenv("OTEL_EXPORTER_OTLP_HEADERS"):
        gcloud_auth = os.getenv("GCLOUD_OTLP_AUTH_HEADER")
        if gcloud_auth:
            os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization={gcloud_auth}"

    if not os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL"):
        # Use HTTP for Grafana Cloud (gRPC has ALPN issues), gRPC for local collectors
        os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf" if is_grafana_cloud else "grpc"


def validate_grafana_cloud_config() -> bool:
    """Validate that Grafana Cloud configuration is present when using cloud endpoints."""
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if "grafana.net" in endpoint or "grafana.com" in endpoint:
        required_vars = [
            "GCLOUD_HOSTED_METRICS_ID",
            "GCLOUD_HOSTED_METRICS_URL",
            "GCLOUD_HOSTED_LOGS_ID",
            "GCLOUD_HOSTED_LOGS_URL",
            "GCLOUD_RW_API_KEY",
            "GCLOUD_OTLP_ENDPOINT",
            "GCLOUD_OTLP_AUTH_HEADER",
        ]
        missing = [var for var in required_vars if not os.getenv(var)]
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
