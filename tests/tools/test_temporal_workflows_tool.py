"""Tests for TemporalWorkflowsTool."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from integrations.temporal.tools import TemporalWorkflowsTool, _map_temporal_workflows
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestTemporalWorkflowsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return TemporalWorkflowsTool()


def test_is_available_when_configured() -> None:
    tool = TemporalWorkflowsTool()
    assert tool.is_available({"temporal": {"base_url": "http://localhost:7233"}}) is True


def test_is_available_when_not_configured() -> None:
    tool = TemporalWorkflowsTool()
    assert tool.is_available({"temporal": {}}) is False
    assert tool.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    tool = TemporalWorkflowsTool()
    sources = mock_agent_state()
    params = tool.extract_params(sources)
    assert params["base_url"] == "http://localhost:7233"
    assert params["namespace"] == "default"
    assert params["api_key"] == ""


def test_run_returns_unavailable_when_no_base_url() -> None:
    tool = TemporalWorkflowsTool()
    result = tool.run(base_url="")
    assert result["available"] is False
    assert "base_url is required" in result["error"]
    assert result["executions"] == []


def test_run_happy_path(monkeypatch) -> None:
    tool = TemporalWorkflowsTool()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.list_workflow_executions.return_value = {
        "success": True,
        "executions": [
            {
                "execution": {"workflowId": "wf-1", "runId": "run-1"},
                "type": {"name": "PaymentWorkflow"},
                "startTime": "2024-01-15T10:00:00Z",
                "closeTime": "2024-01-15T10:05:00Z",
                "status": "WORKFLOW_EXECUTION_STATUS_FAILED",
                "taskQueue": "payment-queue",
                "historyLength": "42",
                "historySizeBytes": "8192",
            }
        ],
        "next_page_token": "",
        "total": 1,
    }

    monkeypatch.setattr(
        "integrations.temporal.tools.TemporalClient",
        lambda _config: mock_client,
    )

    result = tool.run(base_url="http://localhost:7233", namespace="default")
    assert result["available"] is True
    assert result["total"] == 1
    assert result["executions"][0]["status"] == "WORKFLOW_EXECUTION_STATUS_FAILED"
    assert result["executions"][0]["taskQueue"] == "payment-queue"
    assert result["next_page_token"] == ""


def test_run_returns_error_on_failure(monkeypatch) -> None:
    tool = TemporalWorkflowsTool()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.list_workflow_executions.return_value = {
        "success": False,
        "error": "HTTP 401: Unauthorized",
    }

    monkeypatch.setattr(
        "integrations.temporal.tools.TemporalClient",
        lambda _config: mock_client,
    )

    result = tool.run(base_url="http://localhost:7233", namespace="default")
    assert result["available"] is False
    assert "401" in result["error"]
    assert result["executions"] == []


def test_run_passes_pagination_token(monkeypatch) -> None:
    tool = TemporalWorkflowsTool()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.list_workflow_executions.return_value = {
        "success": True,
        "executions": [],
        "next_page_token": "",
        "total": 0,
    }

    monkeypatch.setattr(
        "integrations.temporal.tools.TemporalClient",
        lambda _config: mock_client,
    )

    tool.run(base_url="http://localhost:7233", namespace="default", next_page_token="abc123")
    mock_client.list_workflow_executions.assert_called_once_with(next_page_token="abc123")


class TestMapTemporalWorkflows:
    def test_records_entry_with_failed_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_temporal_workflows(
            evidence,
            {
                "available": True,
                "total": 2,
                "executions": [
                    {"status": "WORKFLOW_EXECUTION_STATUS_COMPLETED"},
                    {"status": "WORKFLOW_EXECUTION_STATUS_FAILED"},
                ],
                "next_page_token": "",
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "temporal_workflows"
        assert entries[0]["summary"] == "2 execution(s), 1 failed/timed-out/terminated"

    def test_includes_pagination_clause(self) -> None:
        evidence: dict[str, Any] = {}

        _map_temporal_workflows(
            evidence,
            {
                "available": True,
                "total": 1,
                "executions": [{"status": "WORKFLOW_EXECUTION_STATUS_RUNNING"}],
                "next_page_token": "tok-2",
            },
            {},
        )

        assert "more available" in evidence["catalog_entries"][0]["summary"]

    def test_records_nothing_when_no_executions(self) -> None:
        evidence: dict[str, Any] = {}

        _map_temporal_workflows(evidence, {"available": True, "executions": []}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_temporal_workflows(evidence, {"available": False, "error": "HTTP 401"}, {})

        assert "catalog_entries" not in evidence
