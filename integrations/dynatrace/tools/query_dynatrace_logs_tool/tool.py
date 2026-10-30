"""Agent-callable bounded Dynatrace log retrieval."""

from __future__ import annotations

from typing import Any

from config.constants.dynatrace import (
    DYNATRACE_DEFAULT_LOG_LIMIT,
    DYNATRACE_DEFAULT_TIMEFRAME_MINUTES,
    DYNATRACE_QUERY_TIMEOUT_SECONDS,
)
from core.domain.types.evidence import record_evidence_entry
from core.tool import BaseTool, EvidenceType, SideEffectLevel
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.dynatrace.client import DynatraceClient
from integrations.dynatrace.config import DynatraceIntegrationConfig
from integrations.dynatrace.tools.query_dynatrace_logs_tool.query import build_logs_query

_SOURCE = "dynatrace"


def _dynatrace_source(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    source = sources.get(_SOURCE)
    return source if isinstance(source, dict) else {}


def _map_query_dynatrace_logs(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    if not output.get("available") or not output.get("records"):
        return
    summary = f"{len(output['records'])} log record(s)"
    if output.get("truncated"):
        summary += " (result limit reached)"
    record_evidence_entry(
        evidence,
        source="query_dynatrace_logs",
        label="Dynatrace Logs",
        summary=summary,
    )


class DynatraceLogsTool(BaseTool):
    """Retrieve recent Grail logs using safe structured filters."""

    name = "query_dynatrace_logs"
    source = _SOURCE
    description = (
        "Query recent Dynatrace Grail logs with bounded Kubernetes, service, level, and "
        "text filters. This tool generates read-only DQL and does not accept arbitrary DQL. "
        "For Kubernetes logs, prefer deployment_name or container_name because service.name "
        "may be absent. Wildcards such as 'checkout-*' are supported by name filters."
    )
    requires = [_SOURCE]
    evidence_type = EvidenceType.LOGS
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    injected_params = ("tenant_url", "platform_token", "integration_id")
    evidence_mapper = _map_query_dynatrace_logs
    input_schema = {
        "type": "object",
        "properties": {
            "deployment_name": {"type": "string", "description": "Kubernetes deployment."},
            "namespace_name": {"type": "string", "description": "Kubernetes namespace."},
            "container_name": {"type": "string", "description": "Kubernetes container."},
            "service_name": {"type": "string", "description": "Service name when enriched."},
            "log_level": {"type": "string", "description": "Log level such as ERROR."},
            "search_text": {"type": "string", "description": "Case-insensitive content text."},
            "timeframe_minutes": {"type": "integer", "default": 15},
            "limit": {"type": "integer", "default": 100},
        },
    }
    outputs = {
        "records": "newest-first Dynatrace log records",
        "query": "generated bounded DQL",
        "truncated": "true when the result limit was reached",
    }

    def is_available(self, sources: dict[str, Any]) -> bool:
        source = _dynatrace_source(sources)
        return bool(
            source.get("connection_verified")
            and source.get("tenant_url")
            and source.get("platform_token")
        )

    def extract_params(self, sources: dict[str, Any]) -> dict[str, Any]:
        source = _dynatrace_source(sources)
        return {
            "tenant_url": source.get("tenant_url"),
            "platform_token": source.get("platform_token"),
            "integration_id": source.get("integration_id"),
        }

    def run(
        self,
        *,
        deployment_name: str = "",
        namespace_name: str = "",
        container_name: str = "",
        service_name: str = "",
        log_level: str = "",
        search_text: str = "",
        timeframe_minutes: int = DYNATRACE_DEFAULT_TIMEFRAME_MINUTES,
        limit: int = DYNATRACE_DEFAULT_LOG_LIMIT,
        tenant_url: str | None = None,
        platform_token: str | None = None,
        integration_id: str | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        try:
            config = DynatraceIntegrationConfig(
                tenant_url=tenant_url or "",
                platform_token=platform_token or "",
                integration_id=integration_id or "",
            )
        except ValueError:
            return tool_unavailable(
                _SOURCE,
                "Dynatrace integration configuration is invalid.",
                error_type="validation_error",
                records=[],
            )
        client = DynatraceClient(config)
        if not client.is_configured:
            return tool_unavailable(
                _SOURCE,
                "Dynatrace integration is not configured.",
                records=[],
            )
        dql, effective_timeframe, effective_limit = build_logs_query(
            deployment_name=deployment_name,
            namespace_name=namespace_name,
            container_name=container_name,
            service_name=service_name,
            log_level=log_level,
            search_text=search_text,
            timeframe_minutes=timeframe_minutes,
            limit=limit,
        )
        result = client.query(dql, timeout_seconds=DYNATRACE_QUERY_TIMEOUT_SECONDS)
        if not result.get("success"):
            return tool_unavailable(
                _SOURCE,
                str(result.get("error", "Unknown Dynatrace error.")),
                error_type=result.get("error_type"),
                records=[],
                query=dql,
            )
        records = result.get("records", [])
        return {
            "source": _SOURCE,
            "available": True,
            "records": records,
            "total": len(records),
            "truncated": len(records) >= effective_limit,
            "timeframe_minutes": effective_timeframe,
            "effective_limit": effective_limit,
            "query": dql,
            "metadata": result.get("metadata", {}),
        }


query_dynatrace_logs = tool(DynatraceLogsTool())

__all__ = ["DynatraceLogsTool", "query_dynatrace_logs"]
