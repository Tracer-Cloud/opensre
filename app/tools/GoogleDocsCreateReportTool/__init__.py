"""Google Docs incident report creation tool."""

from __future__ import annotations

from typing import Any

from app.integrations.models import GoogleDocsIntegrationConfig
from app.services.google_docs import GoogleDocsClient
from app.tools.tool_decorator import tool


def _is_available(sources: dict[str, dict]) -> bool:
    """Check if Google Docs integration is available."""
    return bool(sources.get("google_docs", {}).get("configured"))


def _extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract Google Docs parameters from sources."""
    google_docs = sources.get("google_docs", {})
    return {
        "credentials_file": google_docs.get("credentials_file"),
        "folder_id": google_docs.get("folder_id"),
    }


@tool(
    name="create_google_docs_incident_report",
    source="google_docs",
    description="Create a structured incident postmortem report in Google Docs with investigation findings.",
    use_cases=[
        "Generate a shareable incident report after investigation completes",
        "Create a collaborative postmortem document for team review",
        "Document root cause and remediation steps for stakeholders",
    ],
    requires=["google_docs"],
    input_schema={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Title for the incident report document",
            },
            "summary": {
                "type": "string",
                "description": "Executive summary of the incident",
            },
            "root_cause": {
                "type": "string",
                "description": "Root cause analysis",
            },
            "evidence": {
                "type": "array",
                "description": "List of evidence items with title and description",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                    },
                },
            },
            "timeline": {
                "type": "array",
                "description": "Timeline of incident events",
                "items": {
                    "type": "object",
                    "properties": {
                        "time": {"type": "string"},
                        "description": {"type": "string"},
                    },
                },
            },
            "severity": {
                "type": "string",
                "description": "Incident severity (critical, high, medium, low)",
                "enum": ["critical", "high", "medium", "low"],
            },
            "remediation_steps": {
                "type": "array",
                "description": "Steps taken to remediate the incident",
                "items": {"type": "string"},
            },
            "follow_up_actions": {
                "type": "array",
                "description": "Follow-up action items",
                "items": {"type": "string"},
            },
            "credentials_file": {
                "type": "string",
                "description": "Path to Google service account credentials JSON file",
            },
            "folder_id": {
                "type": "string",
                "description": "Google Drive folder ID where the document will be created",
            },
            "share_with": {
                "type": "array",
                "description": "List of email addresses to share the document with",
                "items": {"type": "string"},
            },
            "share_role": {
                "type": "string",
                "description": "Permission role for shared users (reader, writer, owner). Default is writer.",
                "enum": ["reader", "writer", "owner"],
                "default": "writer",
            },
        },
        "required": [
            "title",
            "summary",
            "root_cause",
            "severity",
            "credentials_file",
            "folder_id",
        ],
    },
    is_available=_is_available,
    extract_params=_extract_params,
)
def create_google_docs_incident_report(
    title: str,
    summary: str,
    root_cause: str,
    severity: str,
    credentials_file: str,
    folder_id: str,
    evidence: list[dict[str, Any]] | None = None,
    timeline: list[dict[str, Any]] | None = None,
    remediation_steps: list[str] | None = None,
    follow_up_actions: list[str] | None = None,
    share_with: list[str] | None = None,
    share_role: str = "writer",
) -> dict[str, Any]:
    """Create a structured incident postmortem report in Google Docs.

    Args:
        title: Title for the incident report document.
        summary: Executive summary of the incident.
        root_cause: Root cause analysis.
        severity: Incident severity (critical, high, medium, low).
        credentials_file: Path to Google service account credentials JSON.
        folder_id: Google Drive folder ID for the document.
        evidence: Optional list of evidence items.
        timeline: Optional timeline of events.
        remediation_steps: Optional remediation steps taken.
        follow_up_actions: Optional follow-up action items.
        share_with: Optional list of emails to share the document with.
        share_role: Permission role for shared users (reader, writer, owner). Default is writer.

    Returns:
        Dictionary with success status, document_url, and document_id.
    """
    try:
        config = GoogleDocsIntegrationConfig(
            credentials_file=credentials_file,
            folder_id=folder_id,
        )
        client = GoogleDocsClient(config)

        if not client.is_configured:
            return {
                "success": False,
                "error": "Google Docs client is not properly configured. Check credentials file and folder ID.",
            }

        # Create the incident report
        result = client.create_incident_report(
            title=title,
            summary=summary,
            root_cause=root_cause,
            evidence=evidence or [],
            timeline=timeline or [],
            severity=severity,
            remediation_steps=remediation_steps,
            follow_up_actions=follow_up_actions,
        )

        if not result.get("success"):
            return result

        # Share with specified users if provided
        if share_with and result.get("document_id"):
            # Validate share_role
            valid_roles = {"reader", "writer", "owner"}
            effective_role = share_role if share_role in valid_roles else "writer"
            for email in share_with:
                client.share_document(result["document_id"], email, role=effective_role)

        return {
            "success": True,
            "document_id": result["document_id"],
            "document_url": result["document_url"],
            "title": result["title"],
            "message": f"Incident report created successfully: {result['document_url']}",
        }

    except Exception as exc:
        return {
            "success": False,
            "error": f"Failed to create incident report: {exc}",
        }
