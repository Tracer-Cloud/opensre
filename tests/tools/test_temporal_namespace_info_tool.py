"""Tests for TemporalNamespaceInfoTool."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from integrations.temporal.tools import TemporalNamespaceInfoTool, _map_temporal_namespace_info
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestTemporalNamespaceInfoToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return TemporalNamespaceInfoTool()


def test_is_available_when_configured() -> None:
    tool = TemporalNamespaceInfoTool()
    assert tool.is_available({"temporal": {"base_url": "http://localhost:7233"}}) is True


def test_is_available_when_not_configured() -> None:
    tool = TemporalNamespaceInfoTool()
    assert tool.is_available({"temporal": {}}) is False
    assert tool.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    tool = TemporalNamespaceInfoTool()
    sources = mock_agent_state()
    params = tool.extract_params(sources)
    assert params["base_url"] == "http://localhost:7233"
    assert params["namespace"] == "default"
    assert params["api_key"] == ""


def test_run_returns_unavailable_when_no_base_url() -> None:
    tool = TemporalNamespaceInfoTool()
    result = tool.run(base_url="")
    assert result["available"] is False
    assert "base_url is required" in result["error"]


def test_run_happy_path(monkeypatch) -> None:
    tool = TemporalNamespaceInfoTool()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get_namespace_info.return_value = {
        "success": True,
        "name": "default",
        "state": "NAMESPACE_STATE_REGISTERED",
        "workflow_count": "58",
        # The client flattens + base64-decodes the raw groupValues into
        # [{"status", "count"}] before returning (see TemporalClient).
        "groups": [
            {"status": "Running", "count": "45"},
            {"status": "Failed", "count": "8"},
            {"status": "TimedOut", "count": "5"},
        ],
    }

    monkeypatch.setattr(
        "integrations.temporal.tools.TemporalClient",
        lambda _config: mock_client,
    )

    result = tool.run(base_url="http://localhost:7233", namespace="default")
    assert result["available"] is True
    assert result["name"] == "default"
    assert result["state"] == "NAMESPACE_STATE_REGISTERED"
    assert result["workflow_count"] == "58"
    assert result["groups"] == [
        {"status": "Running", "count": "45"},
        {"status": "Failed", "count": "8"},
        {"status": "TimedOut", "count": "5"},
    ]


def test_run_returns_error_on_failure(monkeypatch) -> None:
    tool = TemporalNamespaceInfoTool()
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get_namespace_info.return_value = {
        "success": False,
        "error": "HTTP 404: Namespace not found.",
    }

    monkeypatch.setattr(
        "integrations.temporal.tools.TemporalClient",
        lambda _config: mock_client,
    )

    result = tool.run(base_url="http://localhost:7233", namespace="bad-ns")
    assert result["available"] is False
    assert "404" in result["error"]


class TestMapTemporalNamespaceInfo:
    def test_records_entry_with_unhealthy_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_temporal_namespace_info(
            evidence,
            {
                "available": True,
                "name": "default",
                "state": "NAMESPACE_STATE_REGISTERED",
                "workflow_count": "58",
                "groups": [
                    {"status": "Running", "count": "45"},
                    {"status": "Failed", "count": "8"},
                    {"status": "TimedOut", "count": "5"},
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "temporal_namespace_info"
        assert entries[0]["summary"] == (
            "namespace 'default' (NAMESPACE_STATE_REGISTERED), 58 workflow(s), "
            "13 failed/timed-out/terminated"
        )

    def test_records_entry_without_unhealthy_clause_when_no_groups(self) -> None:
        evidence: dict[str, Any] = {}

        _map_temporal_namespace_info(
            evidence,
            {
                "available": True,
                "name": "default",
                "state": "NAMESPACE_STATE_REGISTERED",
                "workflow_count": "0",
                "groups": [],
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == (
            "namespace 'default' (NAMESPACE_STATE_REGISTERED), 0 workflow(s)"
        )

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_temporal_namespace_info(
            evidence, {"available": False, "error": "HTTP 404: Namespace not found."}, {}
        )

        assert "catalog_entries" not in evidence
