"""Google Docs and Drive API client for creating incident postmortem reports."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Any, cast

from app.integrations.models import GoogleDocsIntegrationConfig

logger = logging.getLogger(__name__)


def _handle_google_api_error(exc: Exception, operation: str) -> dict[str, Any]:
    """Handle Google API errors and return user-friendly error messages.

    Args:
        exc: The exception raised by the Google API.
        operation: Description of the operation being performed.

    Returns:
        Dictionary with success=False and descriptive error message.
    """
    error_msg = str(exc)

    # Try to extract HTTP error details
    if hasattr(exc, "resp"):
        status_code = getattr(exc.resp, "status", None)
        if status_code == 403:
            error_msg = (
                f"Insufficient permissions to {operation}. Check service account has access."
            )
        elif status_code == 404:
            error_msg = f"Resource not found while {operation}. Verify folder/document ID."
        elif status_code == 429:
            error_msg = f"Rate limit exceeded while {operation}. Please retry later."
        elif status_code == 400:
            error_msg = f"Invalid request while {operation}. Check parameters."
        elif status_code:
            error_msg = f"HTTP {status_code} error while {operation}: {exc}"

    logger.error("Google API error during %s: %s", operation, exc)
    return {
        "success": False,
        "error": error_msg,
    }


class GoogleDocsClient:
    """Client for creating and managing Google Docs via the Drive and Docs APIs.

    Uses a service account for authentication to avoid OAuth browser flows.
    """

    def __init__(self, config: GoogleDocsIntegrationConfig) -> None:
        self.config = config
        self._docs_service: Any | None = None
        self._drive_service: Any | None = None

    @property
    def is_configured(self) -> bool:
        """Return True if credentials file exists and folder_id is set."""
        return bool(
            self.config.credentials_file
            and Path(self.config.credentials_file).exists()
            and self.config.folder_id
        )

    def _validate_folder_exists(self) -> dict[str, Any]:
        """Check if the configured folder exists and is accessible.

        Returns:
            Dictionary with success status and folder name if accessible.
        """
        try:
            _, drive_service = self._get_services()
            folder = (
                drive_service.files()
                .get(fileId=self.config.folder_id, fields="id,name,mimeType")
                .execute()
            )

            # Verify it's actually a folder
            if folder.get("mimeType") != "application/vnd.google-apps.folder":
                return {
                    "success": False,
                    "error": f"ID {self.config.folder_id} is not a folder (found: {folder.get('mimeType')})",
                }

            return {
                "success": True,
                "folder_id": folder.get("id"),
                "folder_name": folder.get("name"),
            }
        except Exception as exc:
            return _handle_google_api_error(exc, "accessing folder")

    def _get_services(self) -> tuple[Any, Any]:
        """Lazy-load and return (docs_service, drive_service)."""
        if self._docs_service is None or self._drive_service is None:
            try:
                service_account = cast(
                    Any,
                    importlib.import_module("google.oauth2.service_account"),
                )
                googleapiclient_discovery = cast(
                    Any,
                    importlib.import_module("googleapiclient.discovery"),
                )

                credentials = service_account.Credentials.from_service_account_file(
                    self.config.credentials_file,
                    scopes=[
                        "https://www.googleapis.com/auth/documents",
                        "https://www.googleapis.com/auth/drive",
                    ],
                )
                self._docs_service = googleapiclient_discovery.build(
                    "docs",
                    "v1",
                    credentials=credentials,
                )
                self._drive_service = googleapiclient_discovery.build(
                    "drive",
                    "v3",
                    credentials=credentials,
                )
            except Exception as exc:
                logger.error("Failed to initialize Google services: %s", exc)
                raise
        return self._docs_service, self._drive_service

    def create_document(self, title: str, folder_id: str | None = None) -> dict[str, Any]:
        """Create a new Google Doc in the specified Drive folder.

        Args:
            title: The title of the document to create.
            folder_id: Optional folder ID. Uses config.folder_id if not provided.

        Returns:
            Dictionary with document_id, document_url, and title.
        """
        target_folder = folder_id or self.config.folder_id
        if not target_folder:
            return {
                "success": False,
                "error": "No folder_id provided or configured.",
            }

        try:
            _, drive_service = self._get_services()

            file_metadata = {
                "name": title,
                "mimeType": "application/vnd.google-apps.document",
                "parents": [target_folder],
            }

            file = drive_service.files().create(body=file_metadata, fields="id, name").execute()
            document_id = file.get("id")
            document_url = f"https://docs.google.com/document/d/{document_id}/edit"

            return {
                "success": True,
                "document_id": document_id,
                "document_url": document_url,
                "title": title,
            }
        except Exception as exc:
            return _handle_google_api_error(exc, "creating document")

    def insert_content(
        self,
        document_id: str,
        requests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Insert structured content into a Google Doc using batchUpdate.

        Args:
            document_id: The ID of the document to update.
            requests: List of Google Docs API request objects.

        Returns:
            Dictionary with success status and optional error message.
        """
        try:
            docs_service, _ = self._get_services()
            docs_service.documents().batchUpdate(
                documentId=document_id,
                body={"requests": requests},
            ).execute()
            return {"success": True}
        except Exception as exc:
            return _handle_google_api_error(exc, "inserting content")

    def share_document(
        self,
        document_id: str,
        email: str,
        role: str = "writer",
    ) -> dict[str, Any]:
        """Share the document with specified users.

        Args:
            document_id: The ID of the document to share.
            email: The email address to share with.
            role: The permission role (reader, writer, owner). Default is writer.

        Returns:
            Dictionary with success status and optional error message.
        """
        try:
            _, drive_service = self._get_services()
            permission = {
                "type": "user",
                "role": role,
                "emailAddress": email,
            }
            drive_service.permissions().create(
                fileId=document_id,
                body=permission,
                fields="id",
            ).execute()
            return {"success": True}
        except Exception as exc:
            return _handle_google_api_error(exc, "sharing document")

    def get_document(self, document_id: str) -> dict[str, Any]:
        """Retrieve document metadata and content.

        Args:
            document_id: The ID of the document to retrieve.

        Returns:
            Dictionary with document metadata or error information.
        """
        try:
            docs_service, _ = self._get_services()
            document = docs_service.documents().get(documentId=document_id).execute()
            return {
                "success": True,
                "document_id": document.get("documentId"),
                "title": document.get("title"),
                "content": document.get("body", {}),
            }
        except Exception as exc:
            return _handle_google_api_error(exc, "retrieving document")

    def validate_access(self) -> dict[str, Any]:
        """Validate credentials and folder access by listing folder contents.

        Returns:
            Dictionary with success status and folder information.
        """
        try:
            # First validate the folder exists and is actually a folder
            folder_check = self._validate_folder_exists()
            if not folder_check.get("success"):
                return folder_check

            _, drive_service = self._get_services()

            # Test listing the folder to verify access
            results = (
                drive_service.files()
                .list(
                    q=f"'{self.config.folder_id}' in parents",
                    pageSize=10,
                    fields="files(id, name, mimeType)",
                )
                .execute()
            )

            files = results.get("files", [])
            return {
                "success": True,
                "folder_id": self.config.folder_id,
                "folder_name": folder_check.get("folder_name"),
                "file_count": len(files),
                "message": f"Access validated. Folder '{folder_check.get('folder_name')}' contains {len(files)} items.",
            }
        except Exception as exc:
            return _handle_google_api_error(exc, "validating access")

    def create_incident_report(
        self,
        title: str,
        summary: str,
        root_cause: str,
        evidence: list[dict[str, Any]],
        timeline: list[dict[str, Any]],
        severity: str,
        remediation_steps: list[str] | None = None,
        follow_up_actions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a formatted incident postmortem report in Google Docs.

        Args:
            title: Report title.
            summary: Executive summary of the incident.
            root_cause: Root cause analysis.
            evidence: List of evidence items with title and description.
            timeline: List of timeline events with time and description.
            severity: Incident severity (critical, high, medium, low).
            remediation_steps: List of remediation steps taken.
            follow_up_actions: List of follow-up action items.

        Returns:
            Dictionary with success status, document_url, and document_id.
        """
        # Validate folder exists before creating document
        folder_check = self._validate_folder_exists()
        if not folder_check.get("success"):
            return folder_check

        # Create the document
        create_result = self.create_document(title)
        if not create_result.get("success"):
            return create_result

        document_id = create_result["document_id"]
        document_url = create_result["document_url"]

        # Build the content requests
        requests: list[dict[str, Any]] = []

        # Title (Heading 1)
        requests.append(self._create_heading_request(title))

        # Executive Summary Section
        requests.append(self._create_heading_request("Executive Summary"))
        requests.append(self._create_paragraph_request(f"Severity: {severity.upper()}"))
        requests.append(self._create_paragraph_request(summary))
        requests.append(self._create_paragraph_request(""))  # Empty line

        # Root Cause Section
        requests.append(self._create_heading_request("Root Cause"))
        requests.append(self._create_paragraph_request(root_cause))
        requests.append(self._create_paragraph_request(""))

        # Evidence & Signals Section
        requests.append(self._create_heading_request("Evidence & Signals"))
        if evidence:
            for item in evidence:
                item_title = item.get("title", "Untitled")
                item_description = item.get("description", "No description")
                requests.append(
                    self._create_bullet_list_request(f"{item_title}: {item_description}")
                )
        else:
            requests.append(self._create_paragraph_request("No evidence recorded."))
        requests.append(self._create_paragraph_request(""))

        # Timeline Section
        requests.append(self._create_heading_request("Timeline"))
        if timeline:
            for event in timeline:
                event_time = event.get("time", "Unknown time")
                event_description = event.get("description", "No description")
                requests.append(
                    self._create_bullet_list_request(f"[{event_time}] {event_description}")
                )
        else:
            requests.append(self._create_paragraph_request("No timeline recorded."))
        requests.append(self._create_paragraph_request(""))

        # Remediation Steps Section
        requests.append(self._create_heading_request("Remediation Steps"))
        if remediation_steps:
            for step in remediation_steps:
                requests.append(self._create_numbered_list_request(step))
        else:
            requests.append(self._create_paragraph_request("No remediation steps recorded."))
        requests.append(self._create_paragraph_request(""))

        # Follow-up Actions Section
        requests.append(self._create_heading_request("Follow-up Actions"))
        if follow_up_actions:
            for action in follow_up_actions:
                requests.append(self._create_bullet_list_request(action))
        else:
            requests.append(self._create_paragraph_request("No follow-up actions recorded."))

        # Insert all content
        insert_result = self.insert_content(document_id, requests)
        if not insert_result.get("success"):
            return {
                "success": False,
                "error": insert_result.get("error", "Failed to insert content"),
                "document_id": document_id,
            }

        return {
            "success": True,
            "document_id": document_id,
            "document_url": document_url,
            "title": title,
        }

    def _create_heading_request(self, text: str) -> dict[str, Any]:
        """Create a request to insert a heading."""
        return {
            "insertText": {
                "location": {"index": 1},
                "text": f"{text}\n",
            }
        }

    def _create_paragraph_request(self, text: str) -> dict[str, Any]:
        """Create a request to insert a paragraph."""
        return {
            "insertText": {
                "location": {"index": 1},
                "text": f"{text}\n",
            }
        }

    def _create_bullet_list_request(self, text: str) -> dict[str, Any]:
        """Create a request to insert a bullet list item."""
        return {
            "insertText": {
                "location": {"index": 1},
                "text": f"• {text}\n",
            }
        }

    def _create_numbered_list_request(self, text: str) -> dict[str, Any]:
        """Create a request to insert a numbered list item."""
        return {
            "insertText": {
                "location": {"index": 1},
                "text": f"{text}\n",
            }
        }


def build_google_docs_client_from_env() -> GoogleDocsClient | None:
    """Build a GoogleDocsClient from environment variables if available."""
    import os

    credentials_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip()
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()

    if not credentials_file or not folder_id:
        return None

    config = GoogleDocsIntegrationConfig(
        credentials_file=credentials_file,
        folder_id=folder_id,
    )
    return GoogleDocsClient(config)
