"""OpenSearch-compatible analytics tool with bounded retrieval."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from infrastructure.text.truncation import truncate
from integrations.elasticsearch.client import ElasticsearchClient, ElasticsearchConfig
from integrations.elasticsearch.search_failures import not_configured, search_failed

_DEFAULT_MAX_RESULTS = 100
_MAX_HARD_LIMIT = 200
_SUMMARY_FIELD_TRUNCATE_LEN = 80


def _bounded_limit(limit: int, max_results: int) -> int:
    safe_max = max(1, min(max_results, _MAX_HARD_LIMIT))
    return max(1, min(limit, safe_max))


def _opensearch_available(sources: dict[str, dict[str, Any]]) -> bool:
    source = sources.get("opensearch", {})
    return bool(source.get("connection_verified") and source.get("url"))


def _map_query_opensearch_analytics(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the number of log entries retrieved for the query.

    ``query`` and ``index_pattern`` are caller-supplied and unbounded in
    length -- truncate both before embedding them in the summary so a long
    or malformed value can't produce an oversized report line.
    """
    if not output.get("available"):
        return
    logs = output.get("logs") or []
    if not logs:
        return
    query = truncate(str(output.get("query", "*")), _SUMMARY_FIELD_TRUNCATE_LEN)
    index_pattern = truncate(str(output.get("index_pattern", "*")), _SUMMARY_FIELD_TRUNCATE_LEN)
    record_evidence_entry(
        evidence,
        source="query_opensearch_analytics",
        label="OpenSearch Analytics",
        summary=(
            f"{output.get('total_returned', len(logs))} log(s) for query "
            f"'{query}' on '{index_pattern}'"
        ),
    )


def _opensearch_extract_params(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    source = sources["opensearch"]
    return {
        "url": str(source.get("url", "")).strip(),
        "api_key": str(source.get("api_key", "")).strip(),
        "username": str(source.get("username", "")).strip(),
        "password": str(source.get("password", "")).strip(),
        "index_pattern": str(source.get("index_pattern", "*")).strip() or "*",
        "query": str(source.get("default_query", "*")).strip() or "*",
        "time_range_minutes": int(source.get("time_range_minutes", 60) or 60),
        "limit": 50,
        "max_results": int(source.get("max_results", _DEFAULT_MAX_RESULTS) or _DEFAULT_MAX_RESULTS),
        "integration_id": str(source.get("integration_id", "")).strip(),
    }


@tool(
    name="query_opensearch_analytics",
    description="Query OpenSearch-compatible analytics indices with bounded retrieval.",
    source="opensearch",
    surfaces=(ToolSurface.CHAT,),
    requires=["url"],
    input_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "api_key": {"type": "string"},
            "username": {"type": "string"},
            "password": {"type": "string"},
            "index_pattern": {"type": "string", "default": "*"},
            "query": {"type": "string", "default": "*"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "limit": {"type": "integer", "default": 50},
            "max_results": {"type": "integer", "default": 100},
            "integration_id": {"type": "string"},
        },
        "required": ["url"],
    },
    is_available=_opensearch_available,
    injected_params=("url",),
    extract_params=_opensearch_extract_params,
    evidence_mapper=_map_query_opensearch_analytics,
)
def query_opensearch_analytics(
    url: str,
    api_key: str = "",
    username: str = "",
    password: str = "",
    index_pattern: str = "*",
    query: str = "*",
    time_range_minutes: int = 60,
    limit: int = 50,
    max_results: int = _DEFAULT_MAX_RESULTS,
    integration_id: str = "",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Fetch bounded logs from OpenSearch-compatible analytics endpoints."""
    endpoint = url.strip().rstrip("/")
    if not endpoint:
        return not_configured("opensearch", vendor="OpenSearch")

    effective_limit = _bounded_limit(limit, max_results)
    client = ElasticsearchClient(
        ElasticsearchConfig(
            url=endpoint,
            api_key=api_key.strip() or None,
            username=username.strip() or None,
            password=password.strip() or None,
            index_pattern=index_pattern or "*",
        )
    )
    result = client.search_logs(
        query=query or "*",
        time_range_minutes=max(1, time_range_minutes),
        limit=effective_limit,
        index_pattern=index_pattern or "*",
    )
    if not result.get("success"):
        return search_failed("opensearch", result, vendor="OpenSearch")

    logs = result.get("logs", []) if isinstance(result.get("logs"), list) else []
    logs = [log for log in logs if isinstance(log, dict)][:effective_limit]
    return {
        "source": "opensearch",
        "available": True,
        "integration_id": integration_id,
        "index_pattern": index_pattern or "*",
        "query": query or "*",
        "total_returned": len(logs),
        "logs": logs,
    }
