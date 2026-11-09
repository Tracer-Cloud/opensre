# ======== from tools/incident_io_incidents_tool/ ========

"""incident.io incident context and summary write-back tool."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.tool import BaseTool
from core.tool_framework.utils import tool_unavailable
from infrastructure.text.truncation import truncate
from integrations.incident_io.client import make_incident_io_client

#: Incident names are free-form, human-entered text -- unbounded and can
#: contain newlines or carriage returns. Cap the length used in a report
#: summary so one long or multi-line value can't produce a malformed or
#: oversized report line.
_NAME_SUMMARY_TRUNCATE_LEN = 120


def _incident_io_summary_text(value: str) -> str:
    """Collapse and cap free-form incident.io text before it goes into a summary."""
    collapsed = value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return truncate(collapsed, _NAME_SUMMARY_TRUNCATE_LEN)


def _incident_io_page_is_truncated(output: dict[str, Any]) -> bool:
    """A ``pagination_meta.after`` cursor means more results exist beyond this page."""
    return bool((output.get("pagination_meta") or {}).get("after"))


def _incident_io_incident_summary(incident: dict[str, Any]) -> str:
    name = _incident_io_summary_text(str(incident.get("name", "unknown")))
    parts = [f"'{name}'"]
    if incident.get("status"):
        parts.append(str(incident["status"]))
    if incident.get("severity"):
        parts.append(str(incident["severity"]))
    return ", ".join(parts)


def _map_incident_io_incidents(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite incident.io read results; the write action (append_summary) has no
    gathered evidence to cite, so it is intentionally left unhandled here."""
    if not output.get("available"):
        return
    action = output.get("action")

    if action == "list":
        incidents = output.get("incidents") or []
        if not incidents:
            return
        total = output.get("total", len(incidents))
        label = f"{total}+" if _incident_io_page_is_truncated(output) else str(total)
        record_evidence_entry(
            evidence,
            source="incident_io_incidents",
            label="incident.io Incidents",
            summary=f"{label} incident(s)",
        )
    elif action == "get":
        incident = output.get("incident") or {}
        if not incident:
            return
        record_evidence_entry(
            evidence,
            source="incident_io_incidents",
            label="incident.io Incident",
            summary=_incident_io_incident_summary(incident),
        )
    elif action == "updates":
        updates = output.get("incident_updates") or []
        if not updates:
            return
        total = output.get("total", len(updates))
        label = f"{total}+" if _incident_io_page_is_truncated(output) else str(total)
        record_evidence_entry(
            evidence,
            source="incident_io_incidents",
            label="incident.io Incident Updates",
            summary=f"{label} update(s)",
        )
    elif action == "context":
        incident = output.get("incident") or {}
        if not incident:
            return
        parts = [_incident_io_incident_summary(incident)]
        total_updates = output.get("total_updates", 0)
        if total_updates:
            label = (
                f"{total_updates}+"
                if _incident_io_page_is_truncated(output)
                else str(total_updates)
            )
            parts.append(f"{label} update(s)")
        record_evidence_entry(
            evidence,
            source="incident_io_incidents",
            label="incident.io Incident",
            summary=", ".join(parts),
        )


class IncidentIoIncidentsTool(BaseTool):
    """Read incident.io incident context and optionally append OpenSRE findings."""

    name = "incident_io_incidents"
    source = "incident_io"
    evidence_mapper = _map_incident_io_incidents
    description = (
        "Read incident.io incidents, incident metadata, and incident updates for RCA context. "
        "Can append OpenSRE findings to the incident summary through the supported edit endpoint."
    )
    use_cases = [
        "Listing live incident.io incidents related to the current alert",
        "Reading incident metadata, custom fields, roles, timestamps, and updates",
        "Using incident updates as timeline/status context during RCA",
        "Appending investigation findings to the incident summary when explicitly requested",
    ]
    requires = ["api_key"]
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "get", "updates", "context", "append_summary"],
                "default": "context",
                "description": "Action to perform.",
            },
            "status_category": {
                "type": "string",
                "default": "live",
                "description": "Incident status category for list, e.g. live, triage, learning, or empty for all.",
            },
            "page_size": {
                "type": "integer",
                "default": 20,
                "description": "Maximum incidents or updates to return.",
            },
            "after": {
                "type": "string",
                "description": "Pagination cursor from incident.io.",
            },
            "incident_id": {
                "type": "string",
                "description": "incident.io incident ID for get, updates, context, or append_summary.",
            },
            "title": {
                "type": "string",
                "description": "Short title for append_summary.",
            },
            "body": {
                "type": "string",
                "description": "Detailed RCA findings or next steps for append_summary.",
            },
            "notify_incident_channel": {
                "type": "boolean",
                "default": False,
                "description": "Whether incident.io should notify the incident channel on summary update.",
            },
        },
        "required": [],
    }
    outputs = {
        "incidents": "List of incident summaries",
        "incident": "Full incident metadata for a single incident",
        "incident_updates": "Incident update timeline/status messages",
        "success": "Whether the action succeeded",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("incident_io", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        incident_io = sources.get("incident_io", {})
        incident_id = incident_io.get("incident_id", "")
        return {
            "api_key": incident_io.get("api_key", ""),
            "base_url": incident_io.get("base_url", ""),
            "action": "context" if incident_id else "list",
            "incident_id": incident_id,
            "status_category": incident_io.get("status_category", "live"),
            "page_size": incident_io.get("page_size", 20),
        }

    def run(
        self,
        api_key: str,
        *,
        region: str | None = None,
        base_url: str = "",
        action: str = "context",
        status_category: str = "live",
        page_size: int | None = 20,
        after: str | None = None,
        incident_id: str = "",
        title: str = "",
        body: str = "",
        notify_incident_channel: bool = False,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_incident_io_client(api_key, region, base_url=base_url)
        if client is None:
            return tool_unavailable(
                "incident_io", "incident.io integration is not configured.", success=False
            )

        normalized_action = (action or "context").strip().lower()

        def _require_incident_id(label: str) -> dict[str, Any] | None:
            if incident_id:
                return None
            return {"success": False, "error": f"incident_id is required for {label}."}

        def _list() -> dict[str, Any]:
            return client.list_incidents(
                status_category=status_category,
                page_size=page_size,
                after=after,
            )

        def _get() -> dict[str, Any]:
            missing = _require_incident_id("get")
            return missing if missing is not None else client.get_incident(incident_id)

        def _updates() -> dict[str, Any]:
            missing = _require_incident_id("updates")
            return (
                missing
                if missing is not None
                else client.list_incident_updates(
                    incident_id,
                    page_size=page_size,
                    after=after,
                )
            )

        def _append_summary() -> dict[str, Any]:
            missing = _require_incident_id("append_summary")
            if missing is not None:
                return missing
            if not title:
                return {"success": False, "error": "title is required for append_summary."}
            return client.append_summary_update(
                incident_id,
                title=title,
                body=body,
                notify_incident_channel=notify_incident_channel,
            )

        def _context() -> dict[str, Any]:
            missing = _require_incident_id("context")
            return (
                missing
                if missing is not None
                else client.get_incident_context(incident_id, update_limit=page_size)
            )

        handlers = {
            "list": _list,
            "get": _get,
            "updates": _updates,
            "append_summary": _append_summary,
            "context": _context,
        }
        with client:
            result = handlers.get(normalized_action, _context)()

        result.update(
            {
                "source": "incident_io",
                "available": bool(result.get("success")),
                "action": normalized_action,
            }
        )
        return result


incident_io_incidents = IncidentIoIncidentsTool()
