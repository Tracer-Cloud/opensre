# ======== from tools/groundcover_logs_tool/ ========

"""groundcover logs query tool (gcQL over query_logs)."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.groundcover.availability import groundcover_available_or_backend
from integrations.groundcover.client import GroundcoverClient
from integrations.groundcover.guidance import (
    DEFAULT_LOGS_QUERY,
    GCQL_GUIDANCE,
)
from integrations.groundcover.params import base_extract_params
from integrations.groundcover.query_runner import run_signal_query
from integrations.groundcover.tools._evidence import (
    map_get_groundcover_query_reference,
    map_query_groundcover_logs,
    map_query_groundcover_traces,
)

_LOGS_SOURCE = "groundcover_logs"
_LOGS_MCP_TOOL = "query_logs"

_LOGS_QUERY_DESCRIPTION = (
    "gcQL query. Lead with the filter directly (not a '| filter' pipe) and include "
    "'| limit N'. Project raw rows with '| fields ...' rather than a bare select-all. "
    "Examples: 'level:error | fields _time, workload, instance, content | limit 50'; "
    "'workload:checkout level:error | fields _time, instance, content | limit 50'; "
    "'* | stats by (workload) count() if (level:error) as errors | sort by (errors desc) "
    "| limit 20'."
)


def _logs_is_available(sources: dict[str, dict]) -> bool:
    """Available when groundcover credentials are present or a fixture backend is injected."""
    return groundcover_available_or_backend(sources)


def _logs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Inject a pre-built client + seed query, never raw credentials."""
    return base_extract_params(sources.get("groundcover", {}), default_query=DEFAULT_LOGS_QUERY)


@tool(
    name="query_groundcover_logs",
    display_name="groundcover logs",
    source="groundcover",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    tags=("logs", "observability"),
    description=(
        "Search groundcover logs with gcQL. Use for application errors, exceptions, and service "
        "log events. " + GCQL_GUIDANCE + " Discover fields with '* | field_names' or by calling "
        "get_groundcover_query_reference."
    ),
    use_cases=[
        "Finding error/exception logs for a workload or namespace",
        "Correlating log spikes with a groundcover monitor issue",
        "Counting errors per workload with a single stats query over a narrow window",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": _LOGS_QUERY_DESCRIPTION},
            "start": {"type": "string", "description": "RFC3339 start time (optional)"},
            "end": {"type": "string", "description": "RFC3339 end time (optional)"},
            "period": {
                "type": "string",
                "description": "ISO-8601 duration window, e.g. PT1H (default).",
                "default": "PT1H",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    is_available=_logs_is_available,
    extract_params=_logs_extract_params,
    evidence_mapper=map_query_groundcover_logs,
)
def query_groundcover_logs(
    query: str = "",
    start: str = "",
    end: str = "",
    period: str = "",
    _groundcover_client: GroundcoverClient | None = None,
    groundcover_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Search groundcover logs with gcQL and return the normalized OpenSRE envelope.

    Credentials never travel through the model-facing arguments: ``extract_params``
    binds a pre-built :class:`GroundcoverClient` into ``_groundcover_client`` (and an
    optional synthetic ``groundcover_backend``), both stripped from seed input by the
    redactor. An empty query yields a cheap guidance envelope with no MCP round trip.
    """
    return run_signal_query(
        source=_LOGS_SOURCE,
        mcp_tool=_LOGS_MCP_TOOL,
        client=_groundcover_client,
        query=query,
        start=start,
        end=end,
        period=period,
        backend=groundcover_backend,
    )


# ======== from tools/groundcover_query_reference_tool/ ========

"""groundcover query-language (gcQL) reference tool."""


from typing import cast

from core.tool_framework import tool

_QUERY_REF_SOURCE = "groundcover_query_reference"


def _query_ref_is_available(sources: dict[str, dict]) -> bool:
    """Available when groundcover credentials are present or a fixture backend is injected."""
    return groundcover_available_or_backend(sources)


def _query_ref_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Inject a pre-built client (no time window), never raw credentials."""
    return base_extract_params(sources.get("groundcover", {}), include_period=False)


@tool(
    name="get_groundcover_query_reference",
    display_name="groundcover query reference",
    source="groundcover",
    tags=("observability", "reference"),
    surfaces=(ToolSurface.CHAT,),
    description=(
        "Get the groundcover Query Language (gcQL) reference: operators, functions, pipes, and "
        "query patterns. Call this ONCE before writing gcQL for any query_groundcover_* tool. "
        "Reading it first prevents malformed and overly expensive queries."
    ),
    use_cases=[
        "Before composing any non-trivial gcQL query for groundcover logs/traces/metrics/apm",
        "When unsure about stats/sort/filter syntax or pipe operators",
        "To recall the performance and time-window guidance for efficient queries",
    ],
    requires=[],
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    is_available=_query_ref_is_available,
    extract_params=_query_ref_extract_params,
    evidence_mapper=map_get_groundcover_query_reference,
)
def get_groundcover_query_reference(
    _groundcover_client: GroundcoverClient | None = None,
    groundcover_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Return the cached gcQL reference skill content."""
    if groundcover_backend is not None and hasattr(groundcover_backend, "get_query_reference"):
        return cast("dict[str, Any]", groundcover_backend.get_query_reference())

    if _groundcover_client is None:
        return tool_unavailable(
            _QUERY_REF_SOURCE, "groundcover integration not configured", reference=""
        )
    result = _groundcover_client.get_query_reference()
    if not result.get("success"):
        return tool_unavailable(
            _QUERY_REF_SOURCE, result.get("error", "could not fetch gcQL reference"), reference=""
        )
    return {
        "source": _QUERY_REF_SOURCE,
        "available": True,
        "reference": result.get("reference", ""),
        "cached": result.get("cached", False),
        "error": None,
    }


# ======== from tools/groundcover_traces_tool/ ========

"""groundcover traces query tool (gcQL over query_traces)."""


from core.tool_framework import tool
from integrations.groundcover.guidance import (
    DEFAULT_TRACES_QUERY,
    GCQL_GUIDANCE,
)

_TRACES_SOURCE = "groundcover_traces"
_TRACES_MCP_TOOL = "query_traces"

_TRACES_QUERY_DESCRIPTION = (
    "gcQL query. Lead with the filter directly (not a '| filter' pipe) and include "
    "'| limit N'. Project raw spans with '| fields ...' (a bare select-all '| limit N' is "
    "rejected for traces); otherwise aggregate with '| stats ...'. Examples: "
    "'workload:checkout duration_seconds>0.5 | fields _time, span_name, duration_seconds "
    "| sort by (duration_seconds desc) | limit 50'; "
    "'status_code>=500 | stats by (workload) count() as errors | sort by (errors desc) "
    "| limit 20'; "
    "'span_type:mysql status:error | fields _time, span_name, status_code | limit 50'."
)


def _traces_is_available(sources: dict[str, dict]) -> bool:
    """Available when groundcover credentials are present or a fixture backend is injected."""
    return groundcover_available_or_backend(sources)


def _traces_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Inject a pre-built client + seed query, never raw credentials."""
    return base_extract_params(sources.get("groundcover", {}), default_query=DEFAULT_TRACES_QUERY)


@tool(
    name="query_groundcover_traces",
    display_name="groundcover traces",
    source="groundcover",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    tags=("traces", "observability"),
    description=(
        "Query groundcover traces/spans with gcQL. Use to find slow spans, failing spans, and "
        "request correlations across services. " + GCQL_GUIDANCE + " Discover fields with "
        "'* | field_names'. For traces, free text needs '*:*term*' or 'field:*term*' (no bare "
        "keywords). Error filtering by span type: HTTP spans use 'status_code>=500' (or "
        "'status_code>399'); databases/gRPC/messaging and any span type use 'status:error' "
        "(universal). Never use 'status_code>399' on non-HTTP spans — their codes differ. Key "
        "fields: span_name (endpoint), workload (caller), server (callee), duration_seconds, status."
    ),
    use_cases=[
        "Finding the slowest spans for a workload (sort by duration_seconds desc)",
        "Locating 5xx/erroring spans for a service or endpoint",
        "Aggregating error rate and p95 latency per workload with one stats query",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": _TRACES_QUERY_DESCRIPTION},
            "start": {"type": "string", "description": "RFC3339 start time (optional)"},
            "end": {"type": "string", "description": "RFC3339 end time (optional)"},
            "period": {
                "type": "string",
                "description": "ISO-8601 duration window, e.g. PT1H (default).",
                "default": "PT1H",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    is_available=_traces_is_available,
    extract_params=_traces_extract_params,
    evidence_mapper=map_query_groundcover_traces,
)
def query_groundcover_traces(
    query: str = "",
    start: str = "",
    end: str = "",
    period: str = "",
    _groundcover_client: GroundcoverClient | None = None,
    groundcover_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Query groundcover traces/spans with gcQL and return the normalized OpenSRE envelope.

    Credentials never travel through the model-facing arguments: ``extract_params``
    binds a pre-built :class:`GroundcoverClient` into ``_groundcover_client`` (and an
    optional synthetic ``groundcover_backend``), both stripped from seed input by the
    redactor. An empty query yields a cheap guidance envelope with no MCP round trip.
    """
    return run_signal_query(
        source=_TRACES_SOURCE,
        mcp_tool=_TRACES_MCP_TOOL,
        client=_groundcover_client,
        query=query,
        start=start,
        end=end,
        period=period,
        backend=groundcover_backend,
    )
