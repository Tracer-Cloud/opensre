"""SigNoz log search tool."""

from __future__ import annotations

from typing import Any, cast

from core.domain.types.evidence import record_evidence_entry
from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from infrastructure.evidence.evidence_compaction import compact_logs, summarize_counts
from integrations.signoz import (
    SigNozConfig,
    signoz_count_label,
    signoz_effective_limit,
    signoz_extract_params,
)
from integrations.signoz.availability import signoz_available_or_backend
from integrations.signoz.client import SigNozClient


def _map_query_signoz_logs(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Cite the log count retrieved and how many matched an error signal."""
    if not output.get("available"):
        return
    logs = output.get("logs") or []
    if not logs:
        return
    label = signoz_count_label(
        output.get("total", len(logs)), signoz_effective_limit(output, tool_input)
    )
    error_count = len(output.get("error_logs") or [])
    summary = f"{label} log(s)"
    if error_count:
        summary += f", {error_count} error(s)"
    record_evidence_entry(
        evidence,
        source="query_signoz_logs",
        label="SigNoz Logs",
        summary=summary,
    )


def _logs_is_available(sources: dict[str, dict]) -> bool:
    if signoz_available_or_backend(sources):
        return True
    signoz = sources.get("signoz", {})
    return bool(signoz.get("url") and signoz.get("api_key"))


def _logs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    return {
        **signoz_extract_params(sources),
        "service": sources.get("signoz", {}).get("service_name", ""),
        "time_range_minutes": sources.get("signoz", {}).get("time_range_minutes", 60),
        "limit": 50,
        "signoz_backend": sources.get("signoz", {}).get("_backend"),
    }


def _normalize_logs_payload(
    result: dict[str, Any],
    *,
    service: str | None,
) -> dict[str, Any]:
    """Normalize logs output to the canonical envelope expected by the agent."""
    if not result.get("available"):
        return result

    logs = result.get("logs", [])
    error_keywords = ("error", "fail", "exception", "traceback", "panic", "fatal")
    error_logs = [
        log
        for log in logs
        if log.get("severity", "").upper() in ("ERROR", "FATAL", "CRITICAL")
        or any(kw in log.get("message", "").lower() for kw in error_keywords)
    ]

    compacted_logs = compact_logs(logs, limit=50)
    compacted_error_logs = compact_logs(error_logs, limit=30)

    result_data = {
        "source": "signoz_logs",
        "available": True,
        "logs": compacted_logs,
        "error_logs": compacted_error_logs,
        "total": result.get("total", 0),
        "service": service,
    }
    if "effective_limit" in result:
        result_data["effective_limit"] = result["effective_limit"]
    summary = summarize_counts(result.get("total", 0), len(compacted_logs), "logs")
    if summary:
        result_data["truncation_note"] = summary
    return result_data


@tool(
    name="query_signoz_logs",
    display_name="SigNoz logs",
    source="signoz",
    tags=("logs", "observability"),
    description="Query SigNoz logs by service, severity, and time window.",
    use_cases=[
        "Investigating application errors reported by SigNoz alerts",
        "Searching for error logs by service name and severity",
        "Correlating log events with SigNoz trace spans",
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
            "severity": {"type": "string", "description": "Severity filter (e.g. ERROR, WARN)"},
            "limit": {"type": "integer", "default": 50},
        },
        "required": [],
    },
    is_available=_logs_is_available,
    extract_params=_logs_extract_params,
    evidence_mapper=_map_query_signoz_logs,
)
def query_signoz_logs(
    service: str | None = None,
    time_range_minutes: int = 60,
    severity: str | None = None,
    limit: int = 50,
    start_time: str | None = None,
    end_time: str | None = None,
    signoz_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Query SigNoz logs by service, severity, and time window."""
    absolute = (
        {"start_time": start_time, "end_time": end_time}
        if start_time is not None or end_time is not None
        else {}
    )
    if signoz_backend is not None:
        backend_result = cast(
            "dict[str, Any]",
            signoz_backend.query_logs(
                service=service,
                time_range_minutes=time_range_minutes,
                severity=severity,
                limit=limit,
                **absolute,
            ),
        )
        return _normalize_logs_payload(backend_result, service=service)

    config = SigNozConfig.model_validate(_kwargs)
    if not config.is_configured:
        return tool_unavailable(
            "signoz_logs",
            "SigNoz logs not configured. Provide SIGNOZ_URL and SIGNOZ_API_KEY.",
            logs=[],
        )

    client = SigNozClient(config)
    result = client.query_logs(
        service=service,
        time_range_minutes=time_range_minutes,
        severity=severity,
        limit=limit,
        **absolute,
    )
    return _normalize_logs_payload(result, service=service)
