"""Evidence mapper for VictoriaLogs queries."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry


def map_victoria_logs_query(
    evidence: dict[str, Any], output: dict[str, Any], tool_input: dict[str, Any]
) -> None:
    """Lift a VictoriaLogs query result into citeable report evidence."""
    rows = output.get("rows", [])
    if not isinstance(rows, list) or not rows:
        return

    query = output.get("query") or tool_input.get("query")
    query_text = str(query).strip() if query else ""
    row_label = "log entry" if len(rows) == 1 else "log entries"
    record_evidence_entry(
        evidence,
        source="victoria_logs_query",
        label="VictoriaLogs Logs",
        summary=f"{len(rows)} {row_label}",
        snippet=query_text[:200] or None,
    )
