"""Tests for the shared service-client error telemetry helper."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import httpx
import pytest

from infrastructure.observability.errors.service import capture_service_error


@pytest.fixture
def mock_logger() -> logging.Logger:
    return MagicMock(spec=logging.Logger)


def _make_http_status_error(status_code: int = 502, text: str = "error") -> httpx.HTTPStatusError:
    response = httpx.Response(status_code, text=text)
    request = httpx.Request("GET", "https://api.example.com/test")
    return httpx.HTTPStatusError("error", request=request, response=response)


class TestCaptureServiceError:
    def test_server_error_uses_warning_severity(self, mock_logger: logging.Logger) -> None:
        exc = _make_http_status_error(502)
        with patch("infrastructure.observability.errors.service.report_exception") as mock_report:
            capture_service_error(
                exc, logger=mock_logger, integration="jira", method="create_issue"
            )
            assert mock_report.call_args.kwargs["severity"] == "warning"

    def test_503_uses_warning_severity(self, mock_logger: logging.Logger) -> None:
        exc = _make_http_status_error(503)
        with patch("infrastructure.observability.errors.service.report_exception") as mock_report:
            capture_service_error(
                exc, logger=mock_logger, integration="splunk", method="search_logs"
            )
            assert mock_report.call_args.kwargs["severity"] == "warning"

    def test_client_4xx_uses_error_severity(self, mock_logger: logging.Logger) -> None:
        exc = _make_http_status_error(401)
        with patch("infrastructure.observability.errors.service.report_exception") as mock_report:
            capture_service_error(
                exc, logger=mock_logger, integration="jira", method="create_issue"
            )
            assert mock_report.call_args.kwargs["severity"] == "error"

    def test_403_uses_error_severity(self, mock_logger: logging.Logger) -> None:
        exc = _make_http_status_error(403)
        with patch("infrastructure.observability.errors.service.report_exception") as mock_report:
            capture_service_error(
                exc, logger=mock_logger, integration="datadog", method="search_logs"
            )
            assert mock_report.call_args.kwargs["severity"] == "error"

    def test_generic_exception_uses_error_severity(self, mock_logger: logging.Logger) -> None:
        exc = KeyError("items")
        with patch("infrastructure.observability.errors.service.report_exception") as mock_report:
            capture_service_error(
                exc, logger=mock_logger, integration="datadog", method="search_logs"
            )
            assert mock_report.call_args.kwargs["severity"] == "error"

    def test_connection_refused_uses_warning_severity(self, mock_logger: logging.Logger) -> None:
        # Unreachable service: an operational fact, same rule that drops its traceback.
        exc = ConnectionError("refused")
        with patch("infrastructure.observability.errors.service.report_exception") as mock_report:
            capture_service_error(
                exc, logger=mock_logger, integration="datadog", method="search_logs"
            )
            assert mock_report.call_args.kwargs["severity"] == "warning"

    def test_timeout_exception_uses_warning_severity(self, mock_logger: logging.Logger) -> None:
        exc = httpx.ReadTimeout("timed out")
        with patch("infrastructure.observability.errors.service.report_exception") as mock_report:
            capture_service_error(
                exc, logger=mock_logger, integration="splunk", method="search_logs"
            )
            assert mock_report.call_args.kwargs["severity"] == "warning"

    def test_tags_contain_surface_and_integration(self, mock_logger: logging.Logger) -> None:
        exc = RuntimeError("boom")
        with patch("infrastructure.observability.errors.service.report_exception") as mock_report:
            capture_service_error(
                exc, logger=mock_logger, integration="honeycomb", method="run_query"
            )
            tags = mock_report.call_args.kwargs["tags"]
            assert tags == {"surface": "service_client", "integration": "honeycomb"}

    def test_extras_contain_method(self, mock_logger: logging.Logger) -> None:
        exc = RuntimeError("boom")
        with patch("infrastructure.observability.errors.service.report_exception") as mock_report:
            capture_service_error(
                exc, logger=mock_logger, integration="vercel", method="get_runtime_logs"
            )
            extras = mock_report.call_args.kwargs["extras"]
            assert extras == {"method": "get_runtime_logs"}

    def test_caller_extras_merged(self, mock_logger: logging.Logger) -> None:
        exc = _make_http_status_error(502)
        with patch("infrastructure.observability.errors.service.report_exception") as mock_report:
            capture_service_error(
                exc,
                logger=mock_logger,
                integration="datadog",
                method="search_logs",
                extras={"query": "service:web", "time_range_minutes": 60},
            )
            extras = mock_report.call_args.kwargs["extras"]
            assert extras == {
                "method": "search_logs",
                "query": "service:web",
                "time_range_minutes": 60,
            }

    def test_caller_extras_none_defaults_to_method_only(self, mock_logger: logging.Logger) -> None:
        exc = RuntimeError("boom")
        with patch("infrastructure.observability.errors.service.report_exception") as mock_report:
            capture_service_error(exc, logger=mock_logger, integration="jira", method="get_issue")
            assert mock_report.call_args.kwargs["extras"] == {"method": "get_issue"}

    def test_caller_extras_cannot_overwrite_method(self, mock_logger: logging.Logger) -> None:
        exc = RuntimeError("boom")
        with patch("infrastructure.observability.errors.service.report_exception") as mock_report:
            capture_service_error(
                exc,
                logger=mock_logger,
                integration="jira",
                method="create_issue",
                extras={"method": "spoofed", "query": "test"},
            )
            extras = mock_report.call_args.kwargs["extras"]
            assert extras["method"] == "create_issue"
            assert extras["query"] == "test"

    def test_caller_extras_cannot_inject_surface(self, mock_logger: logging.Logger) -> None:
        exc = RuntimeError("boom")
        with patch("infrastructure.observability.errors.service.report_exception") as mock_report:
            capture_service_error(
                exc,
                logger=mock_logger,
                integration="jira",
                method="create_issue",
                extras={"surface": "injected"},
            )
            extras = mock_report.call_args.kwargs["extras"]
            assert "surface" not in extras
            tags = mock_report.call_args.kwargs["tags"]
            assert tags["surface"] == "service_client"

    def test_429_rate_limit_uses_warning_severity(self, mock_logger: logging.Logger) -> None:
        exc = _make_http_status_error(429)
        with patch("infrastructure.observability.errors.service.report_exception") as mock_report:
            capture_service_error(
                exc, logger=mock_logger, integration="datadog", method="search_logs"
            )
            assert mock_report.call_args.kwargs["severity"] == "warning"

    def test_unreachable_service_omits_traceback_by_default(
        self, mock_logger: logging.Logger
    ) -> None:
        """A stopped service is an operational fact; the HTTP stack explains nothing."""
        exc = ConnectionError("refused")
        with patch("infrastructure.observability.errors.service.report_exception") as mock_report:
            capture_service_error(
                exc, logger=mock_logger, integration="kubernetes", method="probe_access"
            )
            assert mock_report.call_args.kwargs["include_traceback"] is False

    def test_unreachable_service_omits_traceback_from_any_method(
        self, mock_logger: logging.Logger
    ) -> None:
        """Classification is by exception, not method: any call can hit a dead host.

        Keyed on the method name instead, one investigation against a stopped
        cluster printed ~180 lines of urllib3 stack across three tool calls.
        """
        exc = ConnectionError("refused")
        with patch("infrastructure.observability.errors.service.report_exception") as mock_report:
            capture_service_error(
                exc, logger=mock_logger, integration="kubernetes", method="list_pods"
            )
            assert mock_report.call_args.kwargs["include_traceback"] is False

    def test_a_genuine_bug_keeps_its_traceback(self, mock_logger: logging.Logger) -> None:
        """Quieting unreachable hosts must not quiet defects."""
        exc = KeyError("items")
        with patch("infrastructure.observability.errors.service.report_exception") as mock_report:
            capture_service_error(
                exc, logger=mock_logger, integration="kubernetes", method="list_pods"
            )
            assert mock_report.call_args.kwargs["include_traceback"] is True

    def test_include_traceback_override_wins(self, mock_logger: logging.Logger) -> None:
        exc = ConnectionError("refused")
        with patch("infrastructure.observability.errors.service.report_exception") as mock_report:
            capture_service_error(
                exc,
                logger=mock_logger,
                integration="kubernetes",
                method="probe_access",
                include_traceback=True,
            )
            assert mock_report.call_args.kwargs["include_traceback"] is True
