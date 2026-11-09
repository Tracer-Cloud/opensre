"""Grafana deployment-annotations query tool for change correlation."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import integrations.grafana.tools._helpers as grafana_helpers
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.grafana.base import _epoch_ms_to_iso, _map_annotation

_GRAFANA_RUNTIME_PARAMS = grafana_helpers.GRAFANA_RUNTIME_PARAMS


def _query_grafana_annotations_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = grafana_helpers._grafana_source(sources)
    return {
        "time_range_minutes": grafana.get("time_range_minutes", 60),
        "grafana_backend": grafana.get("_backend"),
        **grafana_helpers._grafana_creds(grafana),
    }


def _query_grafana_annotations_available(sources: dict[str, dict]) -> bool:
    return grafana_helpers._grafana_available(sources)


def _normalize_backend_annotations(raw: Any) -> list[dict[str, Any]]:
    """Normalize fixture/backend ``/api/annotations`` arrays to the client shape."""
    if not isinstance(raw, list):
        return []
    return [_map_annotation(item) for item in raw if isinstance(item, dict)]


def _iso_to_epoch_ms(value: str) -> int:
    """Parse an ISO 8601 timestamp to epoch milliseconds (UTC). Raises ValueError if invalid.

    A timezone-naive value (no ``Z`` / offset) is interpreted as UTC, not host-local time.
    """
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


@tool(
    name="query_grafana_annotations",
    display_name="Grafana annotations",
    source="grafana",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    description=(
        "Query Grafana deployment/config-change annotations to correlate changes with "
        "an incident — the source-agnostic 'what changed and when' marker."
    ),
    use_cases=[
        "Checking whether a deploy or config change preceded an alert",
        "Correlating incidents with ArgoCD/Flux/Helm/Terraform/manual changes emitted as annotations",
        "Building a source-agnostic change timeline alongside the GitHub deploy timeline",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "from": {
                "type": "string",
                "description": "ISO 8601 window start (overrides time_range_minutes)",
            },
            "to": {
                "type": "string",
                "description": "ISO 8601 window end (overrides time_range_minutes)",
            },
            "tags": {"type": "array", "items": {"type": "string"}},
            "time_range_minutes": {"type": "integer", "default": 60},
            "limit": {"type": "integer", "default": 100},
            "grafana_endpoint": {"type": "string"},
            "grafana_api_key": {"type": "string"},
        },
        "required": [],
    },
    injected_params=_GRAFANA_RUNTIME_PARAMS,
    is_available=_query_grafana_annotations_available,
    extract_params=_query_grafana_annotations_extract_params,
)
def query_grafana_annotations(
    tags: list[str] | None = None,
    time_range_minutes: int = 60,
    limit: int = 100,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_username: str = "",
    grafana_password: str = "",
    grafana_verify_ssl: bool = True,
    grafana_ca_bundle: str = "",
    grafana_backend: Any = None,
    **_kwargs: Any,
) -> dict:
    """Query Grafana annotations to correlate deploys/config changes with an incident.

    ``from``/``to`` are accepted via the schema (ISO 8601); they are read from
    ``_kwargs`` because ``from`` is a Python keyword and cannot be a parameter name.
    When absent, the window defaults to the last ``time_range_minutes``.
    """
    if grafana_backend is not None:
        raw = grafana_backend.query_annotations(tags=tags, limit=limit)
        annotations = _normalize_backend_annotations(raw)
        return {
            "source": "grafana_annotations",
            "available": True,
            "annotations": annotations,
            "total": len(annotations),
            "raw": raw,
        }

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
            "grafana_annotations", "Grafana integration not configured", annotations=[]
        )

    now_ms = int(time.time() * 1000)
    try:
        from_iso, to_iso = _kwargs.get("from"), _kwargs.get("to")
        to_ts = _iso_to_epoch_ms(to_iso) if to_iso else now_ms
        # Default the window to end at `to` (now if unset), so a `to`-only call still
        # yields a valid [to - window, to] range rather than from_ts > to_ts.
        from_ts = _iso_to_epoch_ms(from_iso) if from_iso else to_ts - time_range_minutes * 60 * 1000
    except (ValueError, TypeError, AttributeError) as e:
        return tool_unavailable("grafana_annotations", f"Invalid timestamp: {e}", annotations=[])

    annotations = client.query_annotations(from_ts=from_ts, to_ts=to_ts, tags=tags, limit=limit)
    return {
        "source": "grafana_annotations",
        "available": True,
        "annotations": annotations,
        "total": len(annotations),
        "tags_filter": tags,
        "from": _epoch_ms_to_iso(from_ts),
        "to": _epoch_ms_to_iso(to_ts),
    }


__all__ = ["query_grafana_annotations"]
