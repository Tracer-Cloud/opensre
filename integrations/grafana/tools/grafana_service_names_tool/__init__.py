"""Grafana Loki service name discovery tool."""

from __future__ import annotations

from typing import Any

import integrations.grafana.tools._helpers as grafana_helpers
from core.domain.types.evidence import record_evidence_entry
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable

_GRAFANA_RUNTIME_PARAMS = grafana_helpers.GRAFANA_RUNTIME_PARAMS


def _query_grafana_service_names_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = grafana_helpers._grafana_source(sources)
    return {
        **grafana_helpers._grafana_creds(grafana),
        "grafana_backend": grafana.get("_backend"),
    }


def _query_grafana_service_names_available(sources: dict[str, dict]) -> bool:
    return grafana_helpers._grafana_available(sources)


def _map_grafana_service_names(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    service_names = output.get("service_names", [])
    evidence["grafana_service_names"] = service_names
    if service_names:
        record_evidence_entry(
            evidence,
            source="grafana_service_names",
            label="Grafana Service Names",
            summary=f"{len(service_names)} services",
        )


@tool(
    name="query_grafana_service_names",
    source="grafana",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    evidence_mapper=_map_grafana_service_names,
    description="Discover available service names in Loki.",
    use_cases=[
        "Finding the correct service_name label when query_grafana_logs returns no results",
        "Listing all services that have log data in Grafana Loki",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "grafana_endpoint": {"type": "string"},
            "grafana_api_key": {"type": "string"},
        },
        "required": [],
    },
    injected_params=_GRAFANA_RUNTIME_PARAMS,
    is_available=_query_grafana_service_names_available,
    extract_params=_query_grafana_service_names_extract_params,
)
def query_grafana_service_names(
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_username: str = "",
    grafana_password: str = "",
    grafana_verify_ssl: bool = True,
    grafana_ca_bundle: str = "",
    grafana_backend: Any = None,
    **_kwargs: Any,
) -> dict:
    """Discover available service names in Loki."""
    if grafana_backend is not None:
        return {"source": "grafana_loki_labels", "available": True, "service_names": []}

    client = grafana_helpers._resolve_grafana_client(
        grafana_endpoint,
        grafana_api_key,
        grafana_username,
        grafana_password,
        grafana_verify_ssl,
        grafana_ca_bundle,
    )
    if not client or not client.is_configured:
        return tool_unavailable(
            "grafana_loki_labels", "Grafana integration not configured", service_names=[]
        )

    service_names = client.query_loki_label_values("service_name")
    return {
        "source": "grafana_loki_labels",
        "available": True,
        "service_names": service_names,
    }


__all__ = ["query_grafana_service_names"]
