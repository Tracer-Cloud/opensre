"""Tests for TemporalTaskQueueTool."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from integrations.temporal.tools import TemporalTaskQueueTool, _map_temporal_task_queue
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestTemporalTaskQueueToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return TemporalTaskQueueTool()


def test_is_available_when_configured() -> None:
    tool = TemporalTaskQueueTool()
    assert tool.is_available({"temporal": {"base_url": "http://localhost:7233"}}) is True


def test_is_available_when_not_configured() -> None:
    tool = TemporalTaskQueueTool()
    assert tool.is_available({"temporal": {}}) is False
    assert tool.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    tool = TemporalTaskQueueTool()
    sources = mock_agent_state()
    params = tool.extract_params(sources)
    assert params["base_url"] == "http://localhost:7233"
    assert params["namespace"] == "default"
    assert params["api_key"] == ""


def test_run_returns_unavailable_when_no_base_url() -> None:
    tool = TemporalTaskQueueTool()
    result = tool.run(base_url="", task_queue_name="my-queue")
    assert result["available"] is False
    assert "base_url is required" in result["error"]
    assert result["pollers"] == []
    assert result["stats"] == {}


def test_run_returns_error_when_no_task_queue_name() -> None:
    tool = TemporalTaskQueueTool()
    result = tool.run(base_url="http://localhost:7233", task_queue_name="")
    assert result["available"] is True
    assert "task_queue_name is required" in result["error"]
    assert result["pollers"] == []


def test_run_happy_path(monkeypatch) -> None:
    tool = TemporalTaskQueueTool()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.describe_task_queue.return_value = {
        "success": True,
        "pollers": [
            {
                "lastAccessTime": "2024-01-15T10:05:00Z",
                "identity": "worker-1@host-abc",
                "ratePerSecond": 100.0,
            },
            {
                "lastAccessTime": "2024-01-15T10:04:55Z",
                "identity": "worker-2@host-def",
                "ratePerSecond": 100.0,
            },
        ],
        "stats": {
            "approximateBacklogCount": "42",
            "approximateBacklogAge": "30.5s",
            "tasksAddRate": 5.2,
            "tasksDispatchRate": 4.8,
        },
        "total": 2,
    }

    monkeypatch.setattr(
        "integrations.temporal.tools.TemporalClient",
        lambda _config: mock_client,
    )

    result = tool.run(
        base_url="http://localhost:7233",
        task_queue_name="payment-queue",
        namespace="default",
    )
    assert result["available"] is True
    assert result["total"] == 2
    assert result["pollers"][0]["identity"] == "worker-1@host-abc"
    assert result["stats"]["approximateBacklogCount"] == "42"
    assert result["stats"]["tasksAddRate"] == 5.2

    mock_client.describe_task_queue.assert_called_once_with(task_queue_name="payment-queue")


def test_run_returns_error_on_failure(monkeypatch) -> None:
    tool = TemporalTaskQueueTool()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.describe_task_queue.return_value = {
        "success": False,
        "error": "HTTP 404: Task queue not found.",
    }

    monkeypatch.setattr(
        "integrations.temporal.tools.TemporalClient",
        lambda _config: mock_client,
    )

    result = tool.run(
        base_url="http://localhost:7233",
        task_queue_name="nonexistent-queue",
        namespace="default",
    )
    assert result["available"] is False
    assert "404" in result["error"]
    assert result["pollers"] == []
    assert result["stats"] == {}


class TestMapTemporalTaskQueue:
    def test_records_entry_with_backlog(self) -> None:
        evidence: dict[str, Any] = {}

        _map_temporal_task_queue(
            evidence,
            {
                "available": True,
                "total": 2,
                "pollers": [{"identity": "worker-1"}, {"identity": "worker-2"}],
                "stats": {"approximateBacklogCount": "42"},
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "temporal_task_queue"
        assert entries[0]["summary"] == "2 active poller(s), backlog 42"

    def test_records_zero_pollers_as_the_primary_outage_signal(self) -> None:
        """Regression: an empty poller list means workers are down -- this is
        the tool's main signal, so it must be cited, not treated as noise."""
        evidence: dict[str, Any] = {}

        _map_temporal_task_queue(
            evidence, {"available": True, "total": 0, "pollers": [], "stats": {}}, {}
        )

        assert evidence["catalog_entries"][0]["summary"] == "0 active poller(s)"

    def test_records_nothing_when_task_queue_name_missing(self) -> None:
        """The 'task_queue_name is required' error path returns
        available=True with no 'total' key -- must not be mistaken for a
        genuine zero-poller result."""
        evidence: dict[str, Any] = {}

        _map_temporal_task_queue(
            evidence,
            {
                "available": True,
                "error": "task_queue_name is required.",
                "pollers": [],
                "stats": {},
            },
            {},
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_temporal_task_queue(
            evidence, {"available": False, "error": "HTTP 404: Task queue not found."}, {}
        )

        assert "catalog_entries" not in evidence
