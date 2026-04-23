"""Tests for GoogleDocsCreateReportTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.GoogleDocsCreateReportTool import create_google_docs_incident_report
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestGoogleDocsCreateReportToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return create_google_docs_incident_report.__opensre_registered_tool__


def test_is_available_requires_configured() -> None:
    rt = create_google_docs_incident_report.__opensre_registered_tool__
    assert rt.is_available({"google_docs": {"configured": True}}) is True
    assert rt.is_available({"google_docs": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = create_google_docs_incident_report.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["credentials_file"] == "/path/to/credentials.json"
    assert params["folder_id"] == "abc123folder"


def test_run_returns_failure_when_client_not_configured() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = False
    with patch("app.tools.GoogleDocsCreateReportTool.GoogleDocsClient", return_value=mock_client):
        result = create_google_docs_incident_report(
            title="Incident Report",
            summary="Summary",
            root_cause="Root cause",
            severity="high",
            credentials_file="/path/to/creds.json",
            folder_id="folder-1",
        )
    assert result["success"] is False


def test_run_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.create_incident_report.return_value = {
        "success": True,
        "document_id": "doc-1",
        "document_url": "https://docs.google.com/doc/d/doc-1",
        "title": "Incident Report",
    }
    with patch("app.tools.GoogleDocsCreateReportTool.GoogleDocsClient", return_value=mock_client):
        result = create_google_docs_incident_report(
            title="Incident Report",
            summary="Summary",
            root_cause="Root cause",
            severity="high",
            credentials_file="/path/to/creds.json",
            folder_id="folder-1",
        )
    assert result["success"] is True
    assert result["document_id"] == "doc-1"


def test_run_with_sharing() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.create_incident_report.return_value = {
        "success": True,
        "document_id": "doc-1",
        "document_url": "https://docs.google.com/doc/d/doc-1",
        "title": "Incident Report",
    }
    with patch("app.tools.GoogleDocsCreateReportTool.GoogleDocsClient", return_value=mock_client):
        result = create_google_docs_incident_report(
            title="Incident Report",
            summary="Summary",
            root_cause="Root cause",
            severity="medium",
            credentials_file="/path/to/creds.json",
            folder_id="folder-1",
            share_with=["user@example.com"],
            share_role="writer",
        )
    assert result["success"] is True
    mock_client.share_document.assert_called_once_with("doc-1", "user@example.com", role="writer")


def test_run_handles_exception() -> None:
    with patch(
        "app.tools.GoogleDocsCreateReportTool.GoogleDocsClient", side_effect=Exception("Auth error")
    ):
        result = create_google_docs_incident_report(
            title="Report",
            summary="Summary",
            root_cause="Root cause",
            severity="low",
            credentials_file="/bad/path",
            folder_id="folder-1",
        )
    assert result["success"] is False
    assert "error" in result
