# ======== from tools/tempo_tool/ ========

"""Grafana Tempo trace query tool (single action-based entrypoint)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.tempo import TempoConfig, tempo_extract_params
from integrations.tempo.availability import tempo_available_or_backend
from integrations.tempo.client import TempoClient


def _tempo_is_available(sources: dict[str, dict]) -> bool:
    return tempo_available_or_backend(sources)


def _tempo_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    tempo = sources.get("tempo", {})
    return {
        **tempo_extract_params(sources),
        "service": tempo.get("service_name", ""),
        "time_range_minutes": tempo.get("time_range_minutes", 60),
        "limit": 20,
        "tempo_backend": tempo.get("_backend"),
    }


_VALID_ACTIONS = ("search", "get_trace", "list_services", "list_span_names")


def _map_query_tempo(
    evidence: dict[str, Any], output: dict[str, Any], _input: dict[str, Any]
) -> None:
    for result_key in ("traces", "spans", "services", "span_names"):
        items = output.get(result_key, [])
        if items:
            record_evidence_entry(
                evidence,
                source="query_tempo",
                label="Tempo Query",
                summary=f"{len(items)} {result_key.replace('_', ' ')}",
            )
            return


def _dispatch(
    client: TempoClient,
    *,
    action: str,
    trace_id: str | None,
    service: str | None,
    span_name: str | None,
    min_duration_ms: float | None,
    max_duration_ms: float | None,
    tags: dict[str, str] | None,
    time_range_minutes: int,
    limit: int,
) -> dict[str, Any]:
    """Route one Tempo action through a single handler map."""

    def _search() -> dict[str, Any]:
        return client.search_traces(
            service=service,
            span_name=span_name,
            min_duration_ms=min_duration_ms,
            max_duration_ms=max_duration_ms,
            tags=tags,
            time_range_minutes=time_range_minutes,
            limit=limit,
        )

    handlers: dict[str, Callable[[], dict[str, Any]]] = {
        "search": _search,
        "get_trace": lambda: client.get_trace_by_id(trace_id or ""),
        "list_services": lambda: client.list_services(time_range_minutes=time_range_minutes),
        "list_span_names": lambda: client.list_span_names(time_range_minutes=time_range_minutes),
    }
    # Keys stay aligned with ``_VALID_ACTIONS`` (schema enum + unknown fallback).
    return handlers.get(action, handlers["search"])()


@tool(
    name="query_tempo",
    display_name="Grafana Tempo",
    source="tempo",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    evidence_mapper=_map_query_tempo,
    tags=("traces", "observability"),
    description=(
        "Query a standalone Grafana Tempo backend for distributed traces. "
        "Use 'action' to pick: search traces, fetch a trace by ID, or list "
        "registered services / span names."
    ),
    use_cases=[
        "Fetching a full trace by trace ID to inspect its spans",
        "Searching traces by service, span name, duration, or tags",
        "Listing services and span names registered in Tempo",
        "Correlating slow or error spans with logs and metrics",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": list(_VALID_ACTIONS),
                "default": "search",
                "description": "Which Tempo query to run.",
            },
            "trace_id": {
                "type": "string",
                "description": "Trace ID to fetch (required when action='get_trace').",
            },
            "service": {"type": "string", "description": "Service name filter for search."},
            "span_name": {"type": "string", "description": "Span name filter for search."},
            "min_duration_ms": {"type": "number", "description": "Minimum span duration (ms)."},
            "max_duration_ms": {"type": "number", "description": "Maximum span duration (ms)."},
            "tags": {
                "type": "object",
                "description": "Span attribute filters (key -> value), applied as span.<key>.",
            },
            "time_range_minutes": {"type": "integer", "default": 60},
            "limit": {"type": "integer", "default": 20},
        },
        "required": [],
    },
    is_available=_tempo_is_available,
    extract_params=_tempo_extract_params,
)
def query_tempo(
    action: str = "search",
    trace_id: str | None = None,
    service: str | None = None,
    span_name: str | None = None,
    min_duration_ms: float | None = None,
    max_duration_ms: float | None = None,
    tags: dict[str, str] | None = None,
    time_range_minutes: int = 60,
    limit: int = 20,
    tempo_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Query Grafana Tempo for traces, a single trace, or registered tag values."""
    if action not in _VALID_ACTIONS:
        action = "search"

    if tempo_backend is not None:
        return _dispatch(
            tempo_backend,
            action=action,
            trace_id=trace_id,
            service=service,
            span_name=span_name,
            min_duration_ms=min_duration_ms,
            max_duration_ms=max_duration_ms,
            tags=tags,
            time_range_minutes=time_range_minutes,
            limit=limit,
        )

    config = TempoConfig.model_validate(_kwargs)
    if not config.is_configured:
        return tool_unavailable("tempo", "Tempo not configured. Provide TEMPO_URL.", action=action)

    return _dispatch(
        TempoClient(config),
        action=action,
        trace_id=trace_id,
        service=service,
        span_name=span_name,
        min_duration_ms=min_duration_ms,
        max_duration_ms=max_duration_ms,
        tags=tags,
        time_range_minutes=time_range_minutes,
        limit=limit,
    )
