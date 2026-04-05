from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.OpsGenieAlertsTool import OpsGenieAlertsTool


def _tool() -> OpsGenieAlertsTool:
    return OpsGenieAlertsTool()


def test_is_available_with_connection_verified() -> None:
    assert _tool().is_available({"opsgenie": {"connection_verified": True}}) is True


def test_is_available_false_without_connection_verified() -> None:
    assert _tool().is_available({"opsgenie": {}}) is False
    assert _tool().is_available({}) is False


def test_extract_params_maps_source_fields() -> None:
    sources = {
        "opsgenie": {
            "api_key": "key-1",
            "region": "eu",
            "query": "status=open",
        }
    }
    params = _tool().extract_params(sources)
    assert params["api_key"] == "key-1"
    assert params["region"] == "eu"
    assert params["query"] == "status=open"


@patch("app.tools.OpsGenieAlertsTool.make_opsgenie_client")
def test_run_returns_alerts_and_open_subset(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_alerts.return_value = {
        "success": True,
        "alerts": [
            {"id": "a1", "status": "open", "priority": "P1"},
            {"id": "a2", "status": "closed", "priority": "P3"},
        ],
        "total": 2,
    }
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k", region="us", query="")
    assert result["available"] is True
    assert result["total"] == 2
    assert len(result["open_alerts"]) == 1
    assert result["open_alerts"][0]["id"] == "a1"


@patch("app.tools.OpsGenieAlertsTool.make_opsgenie_client")
def test_run_empty_alerts_list(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_alerts.return_value = {"success": True, "alerts": [], "total": 0}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k")
    assert result["available"] is True
    assert result["alerts"] == []
    assert result["open_alerts"] == []


@patch("app.tools.OpsGenieAlertsTool.make_opsgenie_client")
def test_run_returns_unavailable_on_api_failure(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_alerts.return_value = {"success": False, "error": "HTTP 403"}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k")
    assert result["available"] is False
    assert "403" in result["error"]


@patch("app.tools.OpsGenieAlertsTool.make_opsgenie_client")
def test_run_returns_unavailable_without_key(mock_make: MagicMock) -> None:
    mock_make.return_value = None
    result = _tool().run(api_key="")
    assert result["available"] is False


@patch("app.tools.OpsGenieAlertsTool.make_opsgenie_client")
def test_run_passes_query_and_limit(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.list_alerts.return_value = {"success": True, "alerts": [], "total": 0}
    mock_make.return_value = mock_client

    _tool().run(api_key="k", query="status=open", limit=5)
    mock_client.list_alerts.assert_called_once_with(query="status=open", limit=5)


def test_metadata_is_valid() -> None:
    t = _tool()
    assert t.name == "opsgenie_alerts"
    assert t.source == "opsgenie"
    assert "api_key" in t.input_schema["required"]
