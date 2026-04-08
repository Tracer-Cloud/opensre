from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.OpsGenieAlertDetailTool import OpsGenieAlertDetailTool


def _tool() -> OpsGenieAlertDetailTool:
    return OpsGenieAlertDetailTool()


def test_is_available_requires_connection_verified() -> None:
    assert _tool().is_available({"opsgenie": {"connection_verified": True}}) is True
    assert _tool().is_available({"opsgenie": {}}) is False
    assert _tool().is_available({}) is False


def test_extract_params_maps_source_fields() -> None:
    sources = {
        "opsgenie": {
            "api_key": "key-1",
            "region": "eu",
            "alert_id": "a123",
        }
    }
    params = _tool().extract_params(sources)
    assert params["api_key"] == "key-1"
    assert params["alert_id"] == "a123"
    assert params["region"] == "eu"


@patch("app.tools.OpsGenieAlertDetailTool.make_opsgenie_client")
def test_run_returns_alert_and_activity_log(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_alert.return_value = {
        "success": True,
        "alert": {"id": "a1", "message": "CPU high", "description": "CPU > 90%"},
    }
    mock_client.get_alert_logs.return_value = {
        "success": True,
        "logs": [{"log": "Alert created", "type": "system"}],
        "total": 1,
    }
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k", alert_id="a1")
    assert result["available"] is True
    assert result["alert"]["message"] == "CPU high"
    assert len(result["activity_log"]) == 1
    assert result["total_log_entries"] == 1


@patch("app.tools.OpsGenieAlertDetailTool.make_opsgenie_client")
def test_run_skips_activity_log_when_disabled(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_alert.return_value = {
        "success": True,
        "alert": {"id": "a1", "message": "test"},
    }
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k", alert_id="a1", include_activity_log=False)
    assert result["available"] is True
    assert result["activity_log"] == []
    mock_client.get_alert_logs.assert_not_called()


@patch("app.tools.OpsGenieAlertDetailTool.make_opsgenie_client")
def test_run_handles_alert_fetch_failure(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_alert.return_value = {"success": False, "error": "HTTP 404"}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k", alert_id="bad-id")
    assert result["available"] is False
    assert "404" in result["error"]


@patch("app.tools.OpsGenieAlertDetailTool.make_opsgenie_client")
def test_run_handles_logs_fetch_failure_gracefully(mock_make: MagicMock) -> None:
    mock_client = MagicMock()
    mock_client.get_alert.return_value = {
        "success": True,
        "alert": {"id": "a1", "message": "test"},
    }
    mock_client.get_alert_logs.return_value = {"success": False, "error": "timeout"}
    mock_make.return_value = mock_client

    result = _tool().run(api_key="k", alert_id="a1")
    assert result["available"] is True
    assert result["activity_log"] == []


@patch("app.tools.OpsGenieAlertDetailTool.make_opsgenie_client")
def test_run_returns_unavailable_without_key(mock_make: MagicMock) -> None:
    mock_make.return_value = None
    result = _tool().run(api_key="", alert_id="a1")
    assert result["available"] is False


def test_run_returns_error_without_alert_id() -> None:
    result = _tool().run(api_key="k", alert_id="")
    assert result["available"] is False
    assert "alert_id is required" in result["error"]


def test_metadata_requires_alert_id() -> None:
    t = _tool()
    assert t.name == "opsgenie_alert_detail"
    assert "alert_id" in t.input_schema["required"]
