"""Tests for TemporalWorkflowHistoryTool."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from integrations.temporal.tools import (
    TemporalWorkflowHistoryTool,
    _map_temporal_workflow_history,
)
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestTemporalWorkflowHistoryToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return TemporalWorkflowHistoryTool()


def test_is_available_when_configured() -> None:
    tool = TemporalWorkflowHistoryTool()
    assert tool.is_available({"temporal": {"base_url": "http://localhost:7233"}}) is True


def test_is_available_when_not_configured() -> None:
    tool = TemporalWorkflowHistoryTool()
    assert tool.is_available({"temporal": {}}) is False
    assert tool.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    tool = TemporalWorkflowHistoryTool()
    sources = mock_agent_state()
    params = tool.extract_params(sources)
    assert params["base_url"] == "http://localhost:7233"
    assert params["namespace"] == "default"
    assert params["api_key"] == ""


def test_run_returns_unavailable_when_no_base_url() -> None:
    tool = TemporalWorkflowHistoryTool()
    result = tool.run(base_url="", workflow_id="wf-1")
    assert result["available"] is False
    assert "base_url is required" in result["error"]
    assert result["events"] == []


def test_run_returns_error_when_no_workflow_id() -> None:
    tool = TemporalWorkflowHistoryTool()
    result = tool.run(base_url="http://localhost:7233", workflow_id="")
    assert result["available"] is True
    assert "workflow_id is required" in result["error"]
    assert result["events"] == []


def test_run_happy_path(monkeypatch) -> None:
    tool = TemporalWorkflowHistoryTool()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get_workflow_history.return_value = {
        "success": True,
        "events": [
            {
                "eventId": "1",
                "eventTime": "2024-01-15T10:00:00Z",
                "eventType": "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED",
                "taskId": "1048576",
                "workerMayIgnore": False,
            },
            {
                "eventId": "2",
                "eventTime": "2024-01-15T10:00:05Z",
                "eventType": "EVENT_TYPE_ACTIVITY_TASK_FAILED",
                "taskId": "1048580",
                "workerMayIgnore": False,
            },
            {
                "eventId": "3",
                "eventTime": "2024-01-15T10:00:05Z",
                "eventType": "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED",
                "taskId": "1048581",
                "workerMayIgnore": False,
            },
        ],
        "next_page_token": "",
        "archived": False,
        "total": 3,
    }

    monkeypatch.setattr(
        "integrations.temporal.tools.TemporalClient",
        lambda _config: mock_client,
    )

    result = tool.run(
        base_url="http://localhost:7233",
        workflow_id="wf-1",
        run_id="run-1",
        namespace="default",
    )
    assert result["available"] is True
    assert result["total"] == 3
    assert result["events"][0]["eventType"] == "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED"
    assert result["events"][1]["eventType"] == "EVENT_TYPE_ACTIVITY_TASK_FAILED"
    assert result["events"][2]["eventType"] == "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED"
    assert result["archived"] is False
    assert result["next_page_token"] == ""

    mock_client.get_workflow_history.assert_called_once_with(
        workflow_id="wf-1",
        run_id="run-1",
        next_page_token=None,
    )


def test_run_returns_error_on_failure(monkeypatch) -> None:
    tool = TemporalWorkflowHistoryTool()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get_workflow_history.return_value = {
        "success": False,
        "error": "HTTP 404: Workflow not found.",
    }

    monkeypatch.setattr(
        "integrations.temporal.tools.TemporalClient",
        lambda _config: mock_client,
    )

    result = tool.run(
        base_url="http://localhost:7233",
        workflow_id="nonexistent-wf",
        namespace="default",
    )
    assert result["available"] is False
    assert "404" in result["error"]
    assert result["events"] == []


class TestMapTemporalWorkflowHistory:
    def test_records_entry_with_failure_event_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_temporal_workflow_history(
            evidence,
            {
                "available": True,
                "total": 3,
                "events": [
                    {"eventType": "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED"},
                    {"eventType": "EVENT_TYPE_ACTIVITY_TASK_FAILED"},
                    {"eventType": "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED"},
                ],
                "archived": False,
                "next_page_token": "",
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "temporal_workflow_history"
        assert entries[0]["summary"] == "3 event(s), 2 failure/timeout/termination event(s)"

    def test_includes_archived_and_pagination_clauses(self) -> None:
        evidence: dict[str, Any] = {}

        _map_temporal_workflow_history(
            evidence,
            {
                "available": True,
                "total": 1,
                "events": [{"eventType": "EVENT_TYPE_WORKFLOW_EXECUTION_STARTED"}],
                "archived": True,
                "next_page_token": "tok-2",
            },
            {},
        )

        summary = evidence["catalog_entries"][0]["summary"]
        assert "from archival storage" in summary
        assert "more available" in summary

    def test_records_nothing_when_no_events(self) -> None:
        evidence: dict[str, Any] = {}

        _map_temporal_workflow_history(evidence, {"available": True, "events": []}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_when_workflow_id_missing(self) -> None:
        evidence: dict[str, Any] = {}

        _map_temporal_workflow_history(
            evidence,
            {"available": True, "error": "workflow_id is required.", "events": []},
            {},
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_temporal_workflow_history(
            evidence, {"available": False, "error": "HTTP 404: Workflow not found."}, {}
        )

        assert "catalog_entries" not in evidence
