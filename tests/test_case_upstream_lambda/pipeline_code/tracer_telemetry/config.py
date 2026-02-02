from __future__ import annotations

import os
from typing import Any

from opentelemetry.sdk.resources import Resource


def apply_otel_env_defaults() -> None:
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        gcloud_endpoint = os.getenv("GCLOUD_OTLP_ENDPOINT")
        if gcloud_endpoint:
            os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = gcloud_endpoint

    if not os.getenv("OTEL_EXPORTER_OTLP_HEADERS"):
        gcloud_auth = os.getenv("GCLOUD_OTLP_AUTH_HEADER")
        if gcloud_auth:
            os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization={gcloud_auth}"


def build_resource(service_name: str, extra_attributes: dict[str, Any] | None) -> Resource:
    attributes: dict[str, Any] = {"service.name": service_name}
    if extra_attributes:
        attributes.update(extra_attributes)
    return Resource.create(attributes)
