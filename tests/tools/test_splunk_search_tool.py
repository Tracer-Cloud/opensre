"""Tests for SplunkSearchTool (class-based, BaseTool subclass)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.SplunkSearchTool import SplunkSearchTool
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestSplunkSearchToolContract(BaseToolContract):
    """Shared contract tests via BaseToolContract mixin.

    Automatically runs:
      - test_metadata_has_valid_name
      - test_metadata_has_valid_description
      - test_metadata_has_input_schema
      - test_metadata_has_valid_source
      - test_is_available_returns_bool
      - test_extract_params_returns_dict
    """

    def get_tool_under_test(self):
        return SplunkSearchTool()


# ── metadata ──────────────────────────────────────────────────────────────────


def test_metadata() -> None:
    tool = SplunkSearchTool()
    assert tool.name == "query_splunk_logs"
    assert tool.source == "splunk"
    assert "splunk_logs" in tool.outputs
    assert "splunk_error_logs" in tool.outputs


# ── is_available ──────────────────────────────────────────────────────────────


def test_is_available_requires_connection_verified() -> None:
    tool = SplunkSearchTool()
    assert tool.is_available({"splunk": {"connection_verified": True}}) is True
    assert tool.is_available({"splunk": {"connection_verified": False}}) is False
    assert tool.is_available({"splunk": {}}) is False
    assert tool.is_available({}) is False


# ── extract_params ────────────────────────────────────────────────────────────


def test_extract_params_maps_all_fields() -> None:
    tool = SplunkSearchTool()
    sources = mock_agent_state()
    params = tool.extract_params(sources)

    assert params["base_url"] == "https://splunk.test.corp.com:8089"
    assert params["token"] == "splunk_test_bearer_token"
    assert params["index"] == "main"
    assert "query" in params
    assert params["time_range_minutes"] == 60
    assert params["verify_ssl"] is False


def test_extract_params_uses_default_query_from_sources() -> None:
    tool = SplunkSearchTool()
    sources = mock_agent_state(
        {"splunk": {"default_query": 'index=prod "PaymentTimeout" | head 50'}}
    )
    params = tool.extract_params(sources)
    assert "PaymentTimeout" in params["query"]


def test_extract_params_falls_back_to_index_query_when_no_default() -> None:
    tool = SplunkSearchTool()
    sources = {
        "splunk": {
            "connection_verified": True,
            "base_url": "https://splunk:8089",
            "token": "tok",
            "index": "prod",
            # no default_query
        }
    }
    params = tool.extract_params(sources)
    assert "prod" in params["query"]


# ── run: unavailable paths ────────────────────────────────────────────────────


def test_run_returns_unavailable_when_no_base_url() -> None:
    tool = SplunkSearchTool()
    result = tool.run(query="index=main | head 50", base_url=None, token="tok")
    assert result["available"] is False
    assert result["logs"] == []
    assert "base_url" in result["error"] or "token" in result["error"]


def test_run_returns_unavailable_when_no_token() -> None:
    tool = SplunkSearchTool()
    result = tool.run(query="index=main | head 50", base_url="https://splunk:8089", token="")
    assert result["available"] is False
    assert result["logs"] == []


def test_run_returns_unavailable_on_client_error() -> None:
    tool = SplunkSearchTool()
    mock_client = MagicMock()
    mock_client.search_logs.return_value = {"success": False, "error": "Connection refused"}
    with patch("app.tools.SplunkSearchTool.make_client", return_value=mock_client):
        result = tool.run(
            query="index=main | head 50",
            base_url="https://splunk:8089",
            token="tok",
        )
    assert result["available"] is False
    assert "Connection refused" in result["error"]


def test_run_source_field_is_splunk_logs() -> None:
    tool = SplunkSearchTool()
    result = tool.run(query="index=main | head 50", base_url=None, token=None)
    assert result["source"] == "splunk_logs"


# ── run: happy path ───────────────────────────────────────────────────────────


def test_run_happy_path_separates_error_logs() -> None:
    tool = SplunkSearchTool()
    mock_client = MagicMock()
    mock_client.search_logs.return_value = {
        "success": True,
        "logs": [
            {
                "message": "NullPointerException in PaymentProcessor",
                "timestamp": "2024-01-01T00:00:00Z",
            },
            {"message": "Job started successfully", "timestamp": "2024-01-01T00:00:01Z"},
            {"message": "Connection timeout to database", "timestamp": "2024-01-01T00:00:02Z"},
        ],
        "total": 3,
    }
    with patch("app.tools.SplunkSearchTool.make_client", return_value=mock_client):
        result = tool.run(
            query='index=main "NullPointerException" | head 50',
            base_url="https://splunk:8089",
            token="tok",
        )

    assert result["available"] is True
    assert result["total"] == 3
    assert len(result["logs"]) == 3
    # "exception" matches NullPointerException, "timeout" matches timeout
    assert len(result["error_logs"]) == 2
    assert result["query"] == 'index=main "NullPointerException" | head 50'


def test_run_happy_path_no_error_logs_when_clean() -> None:
    tool = SplunkSearchTool()
    mock_client = MagicMock()
    mock_client.search_logs.return_value = {
        "success": True,
        "logs": [
            {"message": "Job completed successfully", "timestamp": "2024-01-01T00:00:00Z"},
            {"message": "Heartbeat OK", "timestamp": "2024-01-01T00:00:01Z"},
        ],
        "total": 2,
    }
    with patch("app.tools.SplunkSearchTool.make_client", return_value=mock_client):
        result = tool.run(
            query="index=main | head 50",
            base_url="https://splunk:8089",
            token="tok",
        )

    assert result["available"] is True
    assert len(result["error_logs"]) == 0


def test_run_includes_truncation_note_when_results_exceed_limit() -> None:
    tool = SplunkSearchTool()
    many_logs = [
        {"message": f"log entry {i}", "timestamp": "2024-01-01T00:00:00Z"} for i in range(100)
    ]
    mock_client = MagicMock()
    mock_client.search_logs.return_value = {
        "success": True,
        "logs": many_logs,
        "total": 100,
    }
    with patch("app.tools.SplunkSearchTool.make_client", return_value=mock_client):
        result = tool.run(
            query="index=main | head 100",
            base_url="https://splunk:8089",
            token="tok",
        )

    assert len(result["logs"]) == 50  # compact_logs limit
    assert "truncation_note" in result


def test_run_passes_verify_ssl_to_client() -> None:
    tool = SplunkSearchTool()
    captured = {}

    def fake_make_client(_base_url, _token, _index, verify_ssl, ca_bundle=""):
        captured["verify_ssl"] = verify_ssl
        m = MagicMock()
        m.search_logs.return_value = {"success": True, "logs": [], "total": 0}
        return m

    with patch("app.tools.SplunkSearchTool.make_client", side_effect=fake_make_client):
        tool.run(
            query="index=main | head 50",
            base_url="https://splunk:8089",
            token="tok",
            verify_ssl=False,
        )

    assert captured["verify_ssl"] is False


def test_run_passes_ca_bundle_to_client() -> None:
    tool = SplunkSearchTool()
    captured = {}

    def fake_make_client(_base_url, _token, _index, verify_ssl, ca_bundle=""):
        captured["ca_bundle"] = ca_bundle
        m = MagicMock()
        m.search_logs.return_value = {"success": True, "logs": [], "total": 0}
        return m

    with patch("app.tools.SplunkSearchTool.make_client", side_effect=fake_make_client):
        tool.run(
            query="index=main | head 50",
            base_url="https://splunk:8089",
            token="tok",
            ca_bundle="/etc/ssl/certs/corp-ca.pem",
        )

    assert captured["ca_bundle"] == "/etc/ssl/certs/corp-ca.pem"


def test_extract_params_includes_ca_bundle() -> None:
    tool = SplunkSearchTool()
    sources = mock_agent_state()
    params = tool.extract_params(sources)
    assert params["ca_bundle"] == "/etc/ssl/certs/corp-ca.pem"
