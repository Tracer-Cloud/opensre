"""Tests for GrafanaAnnotationsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from integrations.grafana.tools import (
    _iso_to_epoch_ms,
    query_grafana_annotations,
)
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestGrafanaAnnotationsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return query_grafana_annotations.__opensre_registered_tool__


def test_is_available_requires_grafana_creds() -> None:
    rt = query_grafana_annotations.__opensre_registered_tool__
    assert rt.is_available({"grafana": {"connection_verified": True}}) is True
    assert rt.is_available({"grafana": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = query_grafana_annotations.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["grafana_endpoint"] == "https://grafana.example.com"
    assert params["time_range_minutes"] == 60


def test_run_with_backend_maps_wire_shape() -> None:
    mock_backend = MagicMock()
    mock_backend.query_annotations.return_value = [
        {
            "time": 1717079669000,
            "timeEnd": None,
            "text": "deploy checkout-api v2.8.1",
            "tags": ["deployment", "checkout-api"],
            "dashboardUID": "abc",
        }
    ]
    result = query_grafana_annotations(grafana_backend=mock_backend)
    assert result["available"] is True
    assert "raw" in result
    assert result["total"] == 1
    # backend path forwards the filter args (not dropped), so fixtures can filter
    mock_backend.query_annotations.assert_called_once_with(tags=None, limit=100)
    annotation = result["annotations"][0]
    assert isinstance(annotation["time"], str) and annotation["time"].endswith("Z")
    assert annotation["text"] == "deploy checkout-api v2.8.1"
    assert annotation["tags"] == ["deployment", "checkout-api"]
    assert annotation["dashboard_uid"] == "abc"


def test_run_no_client() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = False
    with patch(
        "integrations.grafana.tools._helpers._resolve_grafana_client", return_value=mock_client
    ):
        result = query_grafana_annotations(grafana_endpoint="http://grafana")
    assert result["available"] is False
    assert result["annotations"] == []


def test_run_happy_path_defaults_now_window() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_annotations.return_value = [
        {"time": "2026-05-30T14:41:09Z", "text": "deploy", "tags": ["deployment"]}
    ]
    with patch(
        "integrations.grafana.tools._helpers._resolve_grafana_client", return_value=mock_client
    ):
        result = query_grafana_annotations(grafana_endpoint="http://grafana", time_range_minutes=60)
    assert result["available"] is True
    assert result["total"] == 1
    kwargs = mock_client.query_annotations.call_args.kwargs
    assert isinstance(kwargs["from_ts"], int) and isinstance(kwargs["to_ts"], int)
    # default window spans exactly time_range_minutes (60 min) in epoch ms
    assert kwargs["to_ts"] - kwargs["from_ts"] == 60 * 60 * 1000


def test_run_with_explicit_from_to_override() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_annotations.return_value = []
    with patch(
        "integrations.grafana.tools._helpers._resolve_grafana_client", return_value=mock_client
    ):
        result = query_grafana_annotations(
            grafana_endpoint="http://grafana",
            **{"from": "2026-05-30T14:00:00Z", "to": "2026-05-30T15:00:00Z"},
        )
    mock_client.query_annotations.assert_called_once_with(
        from_ts=_iso_to_epoch_ms("2026-05-30T14:00:00Z"),
        to_ts=_iso_to_epoch_ms("2026-05-30T15:00:00Z"),
        tags=None,
        limit=100,
    )
    # the returned window echoes ISO-8601 strings, consistent with annotation timestamps
    assert result["from"] == "2026-05-30T14:00:00Z"
    assert result["to"] == "2026-05-30T15:00:00Z"


def test_run_forwards_tags_filter() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_annotations.return_value = []
    with patch(
        "integrations.grafana.tools._helpers._resolve_grafana_client", return_value=mock_client
    ):
        result = query_grafana_annotations(grafana_endpoint="http://grafana", tags=["deployment"])
    assert result["tags_filter"] == ["deployment"]
    assert mock_client.query_annotations.call_args.kwargs["tags"] == ["deployment"]


def test_run_invalid_timestamp_returns_error() -> None:
    mock_client = MagicMock()
    mock_client.is_configured = True
    with patch(
        "integrations.grafana.tools._helpers._resolve_grafana_client", return_value=mock_client
    ):
        result = query_grafana_annotations(
            grafana_endpoint="http://grafana", **{"from": "not-a-date"}
        )
    assert result["available"] is False
    assert "Invalid timestamp" in result["error"]


def test_iso_to_epoch_ms_treats_naive_as_utc() -> None:
    # A timezone-naive ISO string must be interpreted as UTC, matching the "Z" form —
    # never as host-local time (which would silently shift the query window).
    assert _iso_to_epoch_ms("2026-05-30T14:00:00") == _iso_to_epoch_ms("2026-05-30T14:00:00Z")


def test_run_backend_forwards_tags_and_limit() -> None:
    mock_backend = MagicMock()
    mock_backend.query_annotations.return_value = []
    query_grafana_annotations(grafana_backend=mock_backend, tags=["deployment"], limit=50)
    mock_backend.query_annotations.assert_called_once_with(tags=["deployment"], limit=50)


def test_extract_params_surfaces_basic_auth() -> None:
    rt = query_grafana_annotations.__opensre_registered_tool__
    sources = mock_agent_state({"grafana": {"username": "admin", "password": "secret"}})
    params = rt.extract_params(sources)
    assert params["grafana_username"] == "admin"
    assert params["grafana_password"] == "secret"


def test_run_forwards_basic_auth_to_client() -> None:
    # Basic-auth Grafana integrations inject username/password; they must reach the client
    # (otherwise /api/annotations is queried without auth headers and returns 401/empty).
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_annotations.return_value = []
    with patch(
        "integrations.grafana.tools._helpers._resolve_grafana_client", return_value=mock_client
    ) as resolve:
        query_grafana_annotations(
            grafana_endpoint="http://grafana",
            grafana_username="admin",
            grafana_password="secret",
        )
    resolve.assert_called_once_with("http://grafana", None, "admin", "secret", True, "")


def test_run_to_only_anchors_window_before_to() -> None:
    # A `to`-only call must yield [to - window, to], not from_ts (now-anchored) > to_ts.
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_annotations.return_value = []
    with patch(
        "integrations.grafana.tools._helpers._resolve_grafana_client", return_value=mock_client
    ):
        query_grafana_annotations(
            grafana_endpoint="http://grafana",
            time_range_minutes=30,
            **{"to": "2026-05-30T15:00:00Z"},
        )
    to_ts = _iso_to_epoch_ms("2026-05-30T15:00:00Z")
    kwargs = mock_client.query_annotations.call_args.kwargs
    assert kwargs["to_ts"] == to_ts
    assert kwargs["from_ts"] == to_ts - 30 * 60 * 1000
