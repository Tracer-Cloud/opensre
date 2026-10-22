# ======== from tools/coralogix_logs_tool/ ========

"""Coralogix log query tool."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.tool import BaseTool, SideEffectLevel
from core.tool_framework.utils import tool_unavailable
from infrastructure.text.truncation import truncate
from integrations.config_models import CoralogixIntegrationConfig
from integrations.coralogix.client import (
    CoralogixClient,
    build_coralogix_logs_query,
)

_ERROR_KEYWORDS = (
    "error",
    "fail",
    "exception",
    "traceback",
    "critical",
    "panic",
    "timeout",
)

#: Scope fields (app/subsystem/trace) echoed into a report summary are
#: caller-supplied and not bounded by the input schema.
_SCOPE_SUMMARY_TRUNCATE_LEN = 60


def _map_query_coralogix_logs(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the log count, keyword-matched error count, and query scope.

    ``client.query_logs`` sends the caller's own ``limit`` straight into the
    DataPrime query with no server-side clamp, so a returned ``total`` at
    that ceiling may understate the true match count -- use the "N+"
    convention against the caller's requested limit.
    """
    if not output.get("available"):
        return
    logs = output.get("logs") or []
    if not logs:
        return
    total = output.get("total", len(logs))
    requested_limit = tool_input.get("limit", 50)
    count_label = f"{total}+" if total >= max(requested_limit, 1) else str(total)
    parts = [f"{count_label} log(s)"]
    error_count = len(output.get("error_logs") or [])
    if error_count:
        parts.append(f"{error_count} matching an error keyword")

    def _safe(value: str) -> str:
        return truncate(value.replace("\n", " "), _SCOPE_SUMMARY_TRUNCATE_LEN)

    scope = []
    if output.get("application_name"):
        scope.append(f"app '{_safe(str(output['application_name']))}'")
    if output.get("subsystem_name"):
        scope.append(f"subsystem '{_safe(str(output['subsystem_name']))}'")
    if output.get("trace_id"):
        scope.append(f"trace '{_safe(str(output['trace_id']))}'")
    if scope:
        parts.append(", ".join(scope))

    record_evidence_entry(
        evidence,
        source="query_coralogix_logs",
        label="Coralogix Logs",
        summary=", ".join(parts),
    )


def _coralogix_available(sources: dict) -> bool:
    return bool(sources.get("coralogix", {}).get("connection_verified"))


def _coralogix_creds(coralogix: dict) -> dict[str, Any]:
    return {
        "coralogix_api_key": coralogix.get("coralogix_api_key"),
        "coralogix_base_url": coralogix.get("coralogix_base_url", "https://api.coralogix.com"),
    }


class CoralogixLogsTool(BaseTool):
    """Query Coralogix DataPrime logs for error signatures and incident context."""

    name = "query_coralogix_logs"
    source = "coralogix"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    evidence_mapper = _map_query_coralogix_logs
    description = "Query Coralogix DataPrime logs for error signatures and incident context."
    use_cases = [
        "Searching Coralogix logs for a failing service or subsystem",
        "Looking up recent errors that match an alert message",
        "Correlating a trace ID with recent Coralogix log events",
    ]
    requires = []
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "limit": {"type": "integer", "default": 50},
            "application_name": {"type": "string"},
            "subsystem_name": {"type": "string"},
            "trace_id": {"type": "string"},
            "coralogix_api_key": {"type": "string"},
            "coralogix_base_url": {"type": "string", "default": "https://api.coralogix.com"},
        },
        "required": ["query"],
    }

    def is_available(self, sources: dict) -> bool:
        return _coralogix_available(sources)

    def extract_params(self, sources: dict) -> dict[str, Any]:
        coralogix = sources["coralogix"]
        return {
            "query": coralogix.get("default_query", "source logs | limit 50"),
            "time_range_minutes": coralogix.get("time_range_minutes", 60),
            "limit": 50,
            "application_name": coralogix.get("application_name", ""),
            "subsystem_name": coralogix.get("subsystem_name", ""),
            "trace_id": coralogix.get("trace_id", ""),
            **_coralogix_creds(coralogix),
        }

    def run(
        self,
        query: str,
        time_range_minutes: int = 60,
        limit: int = 50,
        application_name: str = "",
        subsystem_name: str = "",
        trace_id: str = "",
        coralogix_api_key: str | None = None,
        coralogix_base_url: str = "https://api.coralogix.com",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        config = CoralogixIntegrationConfig.model_validate(
            {
                "api_key": coralogix_api_key or "",
                "base_url": coralogix_base_url,
                "application_name": application_name,
                "subsystem_name": subsystem_name,
            }
        )
        client = CoralogixClient(config)
        if not client.is_configured:
            return tool_unavailable(
                "coralogix_logs", "Coralogix integration is not configured.", logs=[]
            )

        built_query = build_coralogix_logs_query(
            raw_query=query,
            application_name=application_name,
            subsystem_name=subsystem_name,
            trace_id=trace_id,
            limit=limit,
        )
        result = client.query_logs(
            built_query,
            time_range_minutes=time_range_minutes,
            limit=limit,
        )
        if not result.get("success"):
            return tool_unavailable("coralogix_logs", result.get("error", "Unknown error"), logs=[])

        logs = result.get("logs", [])
        error_logs = [
            log
            for log in logs
            if any(keyword in str(log.get("message", "")).lower() for keyword in _ERROR_KEYWORDS)
        ]
        return {
            "source": "coralogix_logs",
            "available": True,
            "logs": logs[:50],
            "error_logs": error_logs[:20],
            "total": result.get("total", 0),
            "query": result.get("query", built_query),
            "application_name": application_name,
            "subsystem_name": subsystem_name,
            "trace_id": trace_id,
            "warnings": result.get("warnings", []),
        }


query_coralogix_logs = CoralogixLogsTool()
