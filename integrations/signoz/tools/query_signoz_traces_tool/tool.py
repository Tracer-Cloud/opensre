"""SigNoz traces query tool."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.signoz import (
    SigNozConfig,
    signoz_count_label,
    signoz_effective_limit,
    signoz_extract_params,
)
from integrations.signoz.availability import signoz_available_or_backend
from integrations.signoz.client import SigNozClient


def _map_query_signoz_traces(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the aggregate span/error/latency summary, or the raw trace count as a fallback.

    ``summary`` comes from a scalar aggregation query (``count()``, ``p99(...)``)
    over the full time window, not a capped row fetch -- it's the true total,
    unlike ``traces`` (a row-limited list). Prefer it whenever it succeeded.
    """
    if not output.get("available"):
        return
    summary = output.get("summary") or {}
    if summary.get("available"):
        total_spans = summary.get("total_spans", 0)
        if not total_spans:
            return
        record_evidence_entry(
            evidence,
            source="query_signoz_traces",
            label="SigNoz Traces",
            summary=(
                f"{total_spans} span(s), {summary.get('error_spans', 0)} error(s), "
                f"p99 {summary.get('p99_ms', 0)}ms"
            ),
        )
        return
    traces = output.get("traces") or []
    if not traces:
        return
    label = signoz_count_label(
        output.get("total", len(traces)), signoz_effective_limit(output, tool_input)
    )
    record_evidence_entry(
        evidence,
        source="query_signoz_traces",
        label="SigNoz Traces",
        summary=f"{label} trace(s)",
    )


def _traces_is_available(sources: dict[str, dict]) -> bool:
    if signoz_available_or_backend(sources):
        return True
    signoz = sources.get("signoz", {})
    return bool(signoz.get("url") and signoz.get("api_key"))


def _traces_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    return {
        **signoz_extract_params(sources),
        "service": sources.get("signoz", {}).get("service_name", ""),
        "time_range_minutes": sources.get("signoz", {}).get("time_range_minutes", 60),
        "error_only": False,
        "limit": 50,
        "signoz_backend": sources.get("signoz", {}).get("_backend"),
    }


@tool(
    name="query_signoz_traces",
    display_name="SigNoz traces",
    source="signoz",
    tags=("traces", "observability"),
    description="Query SigNoz traces for error rate, latency, and slow spans.",
    use_cases=[
        "Investigating slow spans and error traces in SigNoz",
        "Finding p99 latency bottlenecks by service",
        "Correlating trace errors with logs and metrics",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "service": {"type": "string", "description": "Service name filter"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "start_time": {
                "type": "string",
                "description": "Optional absolute ISO-8601 start; requires end_time",
            },
            "end_time": {
                "type": "string",
                "description": "Optional absolute ISO-8601 end; requires start_time",
            },
            "error_only": {"type": "boolean", "default": False},
            "limit": {"type": "integer", "default": 50},
        },
        "required": [],
    },
    is_available=_traces_is_available,
    extract_params=_traces_extract_params,
    evidence_mapper=_map_query_signoz_traces,
)
def query_signoz_traces(
    service: str | None = None,
    time_range_minutes: int = 60,
    error_only: bool = False,
    limit: int = 50,
    start_time: str | None = None,
    end_time: str | None = None,
    signoz_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Query SigNoz traces for error rate, latency, and slow spans."""
    absolute = (
        {"start_time": start_time, "end_time": end_time}
        if start_time is not None or end_time is not None
        else {}
    )
    if signoz_backend is not None:
        traces_result = signoz_backend.query_traces(
            service=service,
            time_range_minutes=time_range_minutes,
            error_only=error_only,
            limit=limit,
            **absolute,
        )
        summary = signoz_backend.query_trace_summary(
            service=service,
            time_range_minutes=time_range_minutes,
            **absolute,
        )
        return {
            **traces_result,
            "summary": summary,
        }

    config = SigNozConfig.model_validate(_kwargs)
    if not config.is_configured:
        return tool_unavailable(
            "signoz_traces",
            "SigNoz traces not configured. Provide SIGNOZ_URL and SIGNOZ_API_KEY.",
            traces=[],
        )

    client = SigNozClient(config)
    traces_result = client.query_traces(
        service=service,
        time_range_minutes=time_range_minutes,
        error_only=error_only,
        limit=limit,
        **absolute,
    )
    summary = client.query_trace_summary(
        service=service,
        time_range_minutes=time_range_minutes,
        **absolute,
    )
    return {
        **traces_result,
        "summary": summary,
    }
