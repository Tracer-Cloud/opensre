"""Unit tests for Google Docs integration client and tool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.google_docs import GoogleDocsClient, build_google_docs_client_from_env
from app.services.google_docs.client import GoogleDocsIntegrationConfig
from app.tools.GoogleDocsCreateReportTool import create_google_docs_incident_report


class TestGoogleDocsIntegrationConfig:
    """Unit tests for GoogleDocsIntegrationConfig model."""

    def test_valid_config(self) -> None:
        """Test valid config creation."""
        config = GoogleDocsIntegrationConfig(
            credentials_file="/path/to/credentials.json",
            folder_id="folder123",
        )
        assert config.credentials_file == "/path/to/credentials.json"
        assert config.folder_id == "folder123"

    def test_config_with_integration_id(self) -> None:
        """Test config with integration_id."""
        config = GoogleDocsIntegrationConfig(
            credentials_file="/path/to/credentials.json",
            folder_id="folder123",
            integration_id="google-docs-1",
        )
        assert config.integration_id == "google-docs-1"

    def test_config_normalizes_whitespace(self) -> None:
        """Test that credentials_file is normalized."""
        config = GoogleDocsIntegrationConfig(
            credentials_file="  /path/to/credentials.json  ",
            folder_id="folder123",
        )
        assert config.credentials_file == "/path/to/credentials.json"


class TestGoogleDocsClient:
    """Unit tests for GoogleDocsClient."""

    def test_is_configured_when_file_exists(self, tmp_path: Path) -> None:
        """Test is_configured returns True when credentials file exists."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"test": "content"}')

        config = GoogleDocsIntegrationConfig(
            credentials_file=str(creds_file),
            folder_id="folder123",
        )
        client = GoogleDocsClient(config)
        assert client.is_configured is True

    def test_is_configured_when_file_missing(self) -> None:
        """Test is_configured returns False when credentials file doesn't exist."""
        config = GoogleDocsIntegrationConfig(
            credentials_file="/nonexistent/path/credentials.json",
            folder_id="folder123",
        )
        client = GoogleDocsClient(config)
        assert client.is_configured is False

    def test_is_configured_when_folder_id_missing(self, tmp_path: Path) -> None:
        """Test is_configured returns False when folder_id is empty."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"test": "content"}')

        config = GoogleDocsIntegrationConfig(
            credentials_file=str(creds_file),
            folder_id="",
        )
        client = GoogleDocsClient(config)
        assert client.is_configured is False

    def test_create_document_no_folder_id(self) -> None:
        """Test create_document fails when no folder_id provided or configured."""
        config = GoogleDocsIntegrationConfig(
            credentials_file="/path/to/creds.json",
            folder_id="",
        )
        client = GoogleDocsClient(config)
        result = client.create_document("Test Report")
        assert result["success"] is False
        assert "No folder_id" in result["error"]

    def test_create_document_success(self, tmp_path: Path) -> None:
        """Test successful document creation."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"test": "content"}')

        config = GoogleDocsIntegrationConfig(
            credentials_file=str(creds_file),
            folder_id="folder123",
        )
        client = GoogleDocsClient(config)

        # Mock the _get_services method
        mock_drive_service = MagicMock()
        mock_files = MagicMock()
        mock_drive_service.files.return_value = mock_files
        mock_create = MagicMock()
        mock_files.create.return_value = mock_create
        mock_create.execute.return_value = {"id": "doc123", "name": "Test Report"}

        with patch.object(client, "_get_services", return_value=(None, mock_drive_service)):
            result = client.create_document("Test Report")

        assert result["success"] is True
        assert result["document_id"] == "doc123"
        assert result["title"] == "Test Report"
        assert result["document_url"] == "https://docs.google.com/document/d/doc123/edit"

    def test_create_document_api_error(self, tmp_path: Path) -> None:
        """Test document creation with API error."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"test": "content"}')

        config = GoogleDocsIntegrationConfig(
            credentials_file=str(creds_file),
            folder_id="folder123",
        )
        client = GoogleDocsClient(config)

        with patch.object(client, "_get_services", side_effect=Exception("API Error")):
            result = client.create_document("Test Report")

        assert result["success"] is False
        # Error message now comes from _handle_google_api_error
        assert "API Error" in result["error"]

    def test_validate_access_success(self, tmp_path: Path) -> None:
        """Test successful validation."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"test": "content"}')

        config = GoogleDocsIntegrationConfig(
            credentials_file=str(creds_file),
            folder_id="folder123",
        )
        client = GoogleDocsClient(config)

        # Mock the Drive service
        mock_drive_service = MagicMock()
        mock_files = MagicMock()
        mock_drive_service.files.return_value = mock_files

        # Mock for _validate_folder_exists (get folder info)
        mock_get = MagicMock()
        mock_get.execute.return_value = {
            "id": "folder123",
            "name": "Incident Reports",
            "mimeType": "application/vnd.google-apps.folder",
        }
        mock_files.get.return_value = mock_get

        # Mock for listing folder contents
        mock_list = MagicMock()
        mock_files.list.return_value = mock_list
        mock_list.execute.return_value = {
            "files": [
                {"id": "1", "name": "file1", "mimeType": "application/pdf"},
                {"id": "2", "name": "file2", "mimeType": "application/vnd.google-apps.document"},
            ]
        }

        with patch.object(client, "_get_services", return_value=(None, mock_drive_service)):
            result = client.validate_access()

        assert result["success"] is True
        assert result["folder_id"] == "folder123"
        assert result["folder_name"] == "Incident Reports"
        assert result["file_count"] == 2

    def test_share_document_success(self, tmp_path: Path) -> None:
        """Test successful document sharing."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"test": "content"}')

        config = GoogleDocsIntegrationConfig(
            credentials_file=str(creds_file),
            folder_id="folder123",
        )
        client = GoogleDocsClient(config)

        # Mock the Drive service
        mock_drive_service = MagicMock()
        mock_permissions = MagicMock()
        mock_drive_service.permissions.return_value = mock_permissions
        mock_create = MagicMock()
        mock_permissions.create.return_value = mock_create
        mock_create.execute.return_value = {"id": "perm123"}

        with patch.object(client, "_get_services", return_value=(None, mock_drive_service)):
            result = client.share_document("doc123", "user@example.com", role="writer")

        assert result["success"] is True

    def test_get_document_success(self, tmp_path: Path) -> None:
        """Test successful document retrieval."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"test": "content"}')

        config = GoogleDocsIntegrationConfig(
            credentials_file=str(creds_file),
            folder_id="folder123",
        )
        client = GoogleDocsClient(config)

        # Mock the Docs service
        mock_docs_service = MagicMock()
        mock_documents = MagicMock()
        mock_docs_service.documents.return_value = mock_documents
        mock_get = MagicMock()
        mock_documents.get.return_value = mock_get
        mock_get.execute.return_value = {
            "documentId": "doc123",
            "title": "Test Document",
            "body": {"content": []},
        }

        with patch.object(client, "_get_services", return_value=(mock_docs_service, None)):
            result = client.get_document("doc123")

        assert result["success"] is True
        assert result["document_id"] == "doc123"
        assert result["title"] == "Test Document"


class TestBuildGoogleDocsClientFromEnv:
    """Unit tests for build_google_docs_client_from_env function."""

    @patch.dict("os.environ", {}, clear=True)
    def test_returns_none_when_env_vars_missing(self) -> None:
        """Test returns None when environment variables are not set."""
        result = build_google_docs_client_from_env()
        assert result is None

    @patch.dict(
        "os.environ",
        {
            "GOOGLE_CREDENTIALS_FILE": "/path/to/creds.json",
            "GOOGLE_DRIVE_FOLDER_ID": "folder123",
        },
        clear=True,
    )
    def test_returns_client_when_env_vars_set(self) -> None:
        """Test returns client when environment variables are set."""
        result = build_google_docs_client_from_env()
        assert result is not None
        assert isinstance(result, GoogleDocsClient)
        assert result.config.credentials_file == "/path/to/creds.json"
        assert result.config.folder_id == "folder123"


class TestGoogleDocsIncidentReportTool:
    """Unit tests for create_google_docs_incident_report tool."""

    @patch("app.tools.GoogleDocsCreateReportTool.GoogleDocsClient")
    def test_create_report_success(
        self,
        mock_client_class: MagicMock,
    ) -> None:
        """Test successful incident report creation."""
        mock_client = MagicMock()
        mock_client.is_configured = True
        mock_client.create_incident_report.return_value = {
            "success": True,
            "document_id": "doc123",
            "document_url": "https://docs.google.com/document/d/doc123/edit",
            "title": "Incident Report: Test",
        }
        mock_client_class.return_value = mock_client

        result = create_google_docs_incident_report(
            title="Incident Report: Test",
            summary="Test incident summary",
            root_cause="Test root cause",
            severity="high",
            credentials_file="/path/to/creds.json",
            folder_id="folder123",
        )

        assert result["success"] is True
        assert result["document_id"] == "doc123"
        assert result["document_url"] == "https://docs.google.com/document/d/doc123/edit"

    @patch("app.tools.GoogleDocsCreateReportTool.GoogleDocsClient")
    def test_create_report_not_configured(
        self,
        mock_client_class: MagicMock,
    ) -> None:
        """Test report creation when client is not configured."""
        mock_client = MagicMock()
        mock_client.is_configured = False
        mock_client_class.return_value = mock_client

        result = create_google_docs_incident_report(
            title="Incident Report: Test",
            summary="Test incident summary",
            root_cause="Test root cause",
            severity="high",
            credentials_file="/path/to/creds.json",
            folder_id="folder123",
        )

        assert result["success"] is False
        assert "not properly configured" in result["error"]

    @patch("app.tools.GoogleDocsCreateReportTool.GoogleDocsClient")
    def test_create_report_with_all_fields(
        self,
        mock_client_class: MagicMock,
    ) -> None:
        """Test report creation with all optional fields."""
        mock_client = MagicMock()
        mock_client.is_configured = True
        mock_client.create_incident_report.return_value = {
            "success": True,
            "document_id": "doc123",
            "document_url": "https://docs.google.com/document/d/doc123/edit",
            "title": "Incident Report: Test",
        }
        mock_client_class.return_value = mock_client

        evidence = [{"title": "Log entry", "description": "Error in logs"}]
        timeline = [{"time": "10:00", "description": "Alert fired"}]
        remediation_steps = ["Step 1", "Step 2"]
        follow_up_actions = ["Action 1", "Action 2"]

        result = create_google_docs_incident_report(
            title="Incident Report: Test",
            summary="Test incident summary",
            root_cause="Test root cause",
            severity="critical",
            credentials_file="/path/to/creds.json",
            folder_id="folder123",
            evidence=evidence,
            timeline=timeline,
            remediation_steps=remediation_steps,
            follow_up_actions=follow_up_actions,
            share_with=["user@example.com"],
        )

        assert result["success"] is True
        # Verify share_document was called
        mock_client.share_document.assert_called_once_with(
            "doc123", "user@example.com", role="writer"
        )

    @patch("app.tools.GoogleDocsCreateReportTool.GoogleDocsClient")
    def test_create_report_api_error(
        self,
        mock_client_class: MagicMock,
    ) -> None:
        """Test report creation when API returns error."""
        mock_client = MagicMock()
        mock_client.is_configured = True
        mock_client.create_incident_report.return_value = {
            "success": False,
            "error": "API Error",
        }
        mock_client_class.return_value = mock_client

        result = create_google_docs_incident_report(
            title="Incident Report: Test",
            summary="Test incident summary",
            root_cause="Test root cause",
            severity="high",
            credentials_file="/path/to/creds.json",
            folder_id="folder123",
        )

        assert result["success"] is False
        assert result["error"] == "API Error"

    def test_tool_is_available_when_configured(self) -> None:
        """Test that is_available returns True when google_docs is configured."""
        from app.tools.GoogleDocsCreateReportTool import _is_available

        sources = {"google_docs": {"configured": True}}
        assert _is_available(sources) is True

    def test_tool_is_available_when_not_configured(self) -> None:
        """Test that is_available returns False when google_docs is not configured."""
        from app.tools.GoogleDocsCreateReportTool import _is_available

        sources = {}
        assert _is_available(sources) is False

    def test_tool_extract_params(self) -> None:
        """Test that extract_params returns correct parameters."""
        from app.tools.GoogleDocsCreateReportTool import _extract_params

        sources = {
            "google_docs": {
                "credentials_file": "/path/to/creds.json",
                "folder_id": "folder123",
            }
        }
        params = _extract_params(sources)
        assert params["credentials_file"] == "/path/to/creds.json"
        assert params["folder_id"] == "folder123"

    @patch("app.tools.GoogleDocsCreateReportTool.GoogleDocsClient")
    def test_create_report_with_custom_share_role(
        self,
        mock_client_class: MagicMock,
    ) -> None:
        """Test report creation with custom share_role parameter."""
        mock_client = MagicMock()
        mock_client.is_configured = True
        mock_client.create_incident_report.return_value = {
            "success": True,
            "document_id": "doc123",
            "document_url": "https://docs.google.com/document/d/doc123/edit",
            "title": "Incident Report: Test",
        }
        mock_client_class.return_value = mock_client

        result = create_google_docs_incident_report(
            title="Incident Report: Test",
            summary="Test incident summary",
            root_cause="Test root cause",
            severity="high",
            credentials_file="/path/to/creds.json",
            folder_id="folder123",
            share_with=["user@example.com"],
            share_role="reader",
        )

        assert result["success"] is True
        # Verify share_document was called with reader role
        mock_client.share_document.assert_called_once_with(
            "doc123", "user@example.com", role="reader"
        )

    @patch("app.tools.GoogleDocsCreateReportTool.GoogleDocsClient")
    def test_create_report_with_invalid_share_role_defaults_to_writer(
        self,
        mock_client_class: MagicMock,
    ) -> None:
        """Test that invalid share_role defaults to writer."""
        mock_client = MagicMock()
        mock_client.is_configured = True
        mock_client.create_incident_report.return_value = {
            "success": True,
            "document_id": "doc123",
            "document_url": "https://docs.google.com/document/d/doc123/edit",
            "title": "Incident Report: Test",
        }
        mock_client_class.return_value = mock_client

        result = create_google_docs_incident_report(
            title="Incident Report: Test",
            summary="Test incident summary",
            root_cause="Test root cause",
            severity="high",
            credentials_file="/path/to/creds.json",
            folder_id="folder123",
            share_with=["user@example.com"],
            share_role="invalid_role",
        )

        assert result["success"] is True
        # Verify share_document was called with writer role (default)
        mock_client.share_document.assert_called_once_with(
            "doc123", "user@example.com", role="writer"
        )


class TestGoogleDocsIntegrationConfigTimeout:
    """Unit tests for GoogleDocsIntegrationConfig timeout validation."""

    def test_default_timeout(self) -> None:
        """Test default timeout is 30 seconds."""
        config = GoogleDocsIntegrationConfig(
            credentials_file="/path/to/creds.json",
            folder_id="folder123",
        )
        assert config.timeout_seconds == 30

    def test_custom_timeout(self) -> None:
        """Test custom timeout can be set."""
        config = GoogleDocsIntegrationConfig(
            credentials_file="/path/to/creds.json",
            folder_id="folder123",
            timeout_seconds=60,
        )
        assert config.timeout_seconds == 60

    def test_timeout_minimum_bound(self) -> None:
        """Test timeout has minimum of 5 seconds."""
        config = GoogleDocsIntegrationConfig(
            credentials_file="/path/to/creds.json",
            folder_id="folder123",
            timeout_seconds=1,  # Should be clamped to 5
        )
        assert config.timeout_seconds == 5

    def test_timeout_maximum_bound(self) -> None:
        """Test timeout has maximum of 300 seconds."""
        config = GoogleDocsIntegrationConfig(
            credentials_file="/path/to/creds.json",
            folder_id="folder123",
            timeout_seconds=600,  # Should be clamped to 300
        )
        assert config.timeout_seconds == 300

    def test_timeout_invalid_value_defaults_to_30(self) -> None:
        """Test invalid timeout value defaults to 30."""
        config = GoogleDocsIntegrationConfig(
            credentials_file="/path/to/creds.json",
            folder_id="folder123",
            timeout_seconds="invalid",  # type: ignore[arg-type]
        )
        assert config.timeout_seconds == 30


class TestGoogleDocsClientIncidentReport:
    """Unit tests for create_incident_report method."""

    @patch.object(GoogleDocsClient, "_validate_folder_exists")
    @patch.object(GoogleDocsClient, "create_document")
    @patch.object(GoogleDocsClient, "insert_content")
    def test_create_incident_report_with_all_params(
        self,
        mock_insert: MagicMock,
        mock_create: MagicMock,
        mock_validate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test create_incident_report with all parameters."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"test": "content"}')

        mock_validate.return_value = {"success": True, "folder_name": "Test Folder"}
        mock_create.return_value = {
            "success": True,
            "document_id": "doc123",
            "document_url": "https://docs.google.com/document/d/doc123/edit",
        }
        mock_insert.return_value = {"success": True}

        config = GoogleDocsIntegrationConfig(
            credentials_file=str(creds_file),
            folder_id="folder123",
        )
        client = GoogleDocsClient(config)

        evidence = [
            {"title": "Log Error", "description": "Database connection failed"},
            {"title": "Metric Spike", "description": "CPU usage at 100%"},
        ]
        timeline = [
            {"time": "10:00 UTC", "description": "Alert triggered"},
            {"time": "10:15 UTC", "description": "Investigation started"},
            {"time": "11:30 UTC", "description": "Issue resolved"},
        ]
        remediation_steps = ["Restarted database", "Scaled up instances"]
        follow_up_actions = ["Add monitoring", "Review capacity planning"]

        result = client.create_incident_report(
            title="Incident: Database Outage",
            summary="Database connection pool exhausted causing service degradation",
            root_cause="Connection leak in application code",
            evidence=evidence,
            timeline=timeline,
            severity="critical",
            remediation_steps=remediation_steps,
            follow_up_actions=follow_up_actions,
        )

        assert result["success"] is True
        assert result["document_id"] == "doc123"
        mock_validate.assert_called_once()
        mock_create.assert_called_once_with("Incident: Database Outage")
        mock_insert.assert_called_once()

    @patch.object(GoogleDocsClient, "_validate_folder_exists")
    def test_create_incident_report_folder_not_found(
        self,
        mock_validate: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test create_incident_report when folder doesn't exist."""
        creds_file = tmp_path / "credentials.json"
        creds_file.write_text('{"test": "content"}')

        mock_validate.return_value = {
            "success": False,
            "error": "Folder not found",
        }

        config = GoogleDocsIntegrationConfig(
            credentials_file=str(creds_file),
            folder_id="invalid_folder",
        )
        client = GoogleDocsClient(config)

        result = client.create_incident_report(
            title="Test Report",
            summary="Test summary",
            root_cause="Test cause",
            evidence=[],
            timeline=[],
            severity="high",
        )

        assert result["success"] is False
        assert "Folder not found" in result["error"]


class TestGoogleDocsClientErrorHandling:
    """Unit tests for Google API error handling."""

    def test_handle_google_api_error_with_403(self) -> None:
        """Test handling of 403 permission error."""
        from app.services.google_docs.client import _handle_google_api_error

        class MockHttpError(Exception):
            def __init__(self) -> None:
                super().__init__()
                self.resp = type("obj", (object,), {"status": 403})()

        exc = MockHttpError()
        result = _handle_google_api_error(exc, "creating document")

        assert result["success"] is False
        assert "Insufficient permissions" in result["error"]

    def test_handle_google_api_error_with_404(self) -> None:
        """Test handling of 404 not found error."""
        from app.services.google_docs.client import _handle_google_api_error

        class MockHttpError(Exception):
            def __init__(self) -> None:
                super().__init__()
                self.resp = type("obj", (object,), {"status": 404})()

        exc = MockHttpError()
        result = _handle_google_api_error(exc, "accessing folder")

        assert result["success"] is False
        assert "Resource not found" in result["error"]

    def test_handle_google_api_error_with_429(self) -> None:
        """Test handling of 429 rate limit error."""
        from app.services.google_docs.client import _handle_google_api_error

        class MockHttpError(Exception):
            def __init__(self) -> None:
                super().__init__()
                self.resp = type("obj", (object,), {"status": 429})()

        exc = MockHttpError()
        result = _handle_google_api_error(exc, "inserting content")

        assert result["success"] is False
        assert "Rate limit exceeded" in result["error"]

    def test_handle_google_api_error_generic(self) -> None:
        """Test handling of generic error."""
        from app.services.google_docs.client import _handle_google_api_error

        exc = Exception("Something went wrong")
        result = _handle_google_api_error(exc, "sharing document")

        assert result["success"] is False
        assert "Something went wrong" in result["error"]
