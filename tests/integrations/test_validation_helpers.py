"""Tests for integrations._validation_helpers."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from pydantic import BaseModel, ValidationError

from integrations._validation_helpers import report_classify_failure, report_validation_failure


def _mock_logger() -> MagicMock:
    return MagicMock(spec=logging.Logger)


class TestReportValidationFailure:
    def test_default_severity_is_warning(self) -> None:
        mock_log = _mock_logger()
        exc = RuntimeError("boom")
        with patch("infrastructure.observability.errors.boundary.capture_exception"):
            report_validation_failure(
                exc,
                logger=mock_log,
                integration="trello",
                method="validate_trello_config",
            )
        mock_log.warning.assert_called_once()
        mock_log.error.assert_not_called()

    def test_message_includes_integration_and_method(self) -> None:
        mock_log = _mock_logger()
        with patch("infrastructure.observability.errors.boundary.capture_exception"):
            report_validation_failure(
                RuntimeError("x"),
                logger=mock_log,
                integration="kafka",
                method="get_topic_health",
            )
        message = mock_log.warning.call_args[0][1]
        assert message == "[kafka] get_topic_health validation failed"

    def test_tags_have_expected_shape(self) -> None:
        mock_log = _mock_logger()
        exc = RuntimeError("boom")
        with patch("infrastructure.observability.errors.boundary.capture_exception") as mock_cap:
            report_validation_failure(
                exc,
                logger=mock_log,
                integration="postgresql",
                method="get_server_status",
            )
        extra = mock_cap.call_args[1]["extra"]
        assert extra["tag.surface"] == "integration"
        assert extra["tag.integration"] == "postgresql"
        assert extra["tag.event"] == "validation_failed"
        assert extra["tag.method"] == "get_server_status"

    def test_extras_pass_through_unprefixed(self) -> None:
        mock_log = _mock_logger()
        with patch("infrastructure.observability.errors.boundary.capture_exception") as mock_cap:
            report_validation_failure(
                RuntimeError("x"),
                logger=mock_log,
                integration="airflow",
                method="get_recent_airflow_failures.task_instances",
                extras={"dag_id": "dag-42", "dag_run_id": "run-7"},
            )
        extra = mock_cap.call_args[1]["extra"]
        assert extra["dag_id"] == "dag-42"
        assert extra["dag_run_id"] == "run-7"
        # extras should NOT be prefixed with "tag." (they're not Sentry tags)
        assert "tag.dag_id" not in extra
        assert "tag.dag_run_id" not in extra

    def test_severity_override_routes_to_logger(self) -> None:
        mock_log = _mock_logger()
        with patch("infrastructure.observability.errors.boundary.capture_exception"):
            report_validation_failure(
                RuntimeError("x"),
                logger=mock_log,
                integration="mongodb",
                method="get_server_status",
                severity="error",
            )
        mock_log.error.assert_called_once()
        mock_log.warning.assert_not_called()

    def test_default_suppresses_terminal_traceback(self) -> None:
        """Validator failures must not dump a stack trace into the REPL by default."""
        mock_log = _mock_logger()
        with patch("infrastructure.observability.errors.boundary.capture_exception"):
            report_validation_failure(
                RuntimeError("boom"),
                logger=mock_log,
                integration="github_mcp",
                method="validate_github_mcp_config",
            )
        assert mock_log.warning.call_args.kwargs["exc_info"] is False

    def test_traceback_included_when_explicitly_requested(self) -> None:
        mock_log = _mock_logger()
        exc = RuntimeError("boom")
        with patch("infrastructure.observability.errors.boundary.capture_exception"):
            report_validation_failure(
                exc,
                logger=mock_log,
                integration="github_mcp",
                method="validate_github_mcp_config",
                include_traceback=True,
            )
        assert mock_log.warning.call_args.kwargs["exc_info"] is exc

    def test_captures_to_sentry_exactly_once(self) -> None:
        mock_log = _mock_logger()
        exc = RuntimeError("once")
        with patch("infrastructure.observability.errors.boundary.capture_exception") as mock_cap:
            report_validation_failure(
                exc,
                logger=mock_log,
                integration="mysql",
                method="validate_mysql_config",
            )
        mock_cap.assert_called_once()
        assert mock_cap.call_args[0][0] is exc


class _SecretConfig(BaseModel):
    api_token: str
    port: int


class TestReportClassifyFailure:
    def test_validation_error_is_wrapped_before_reporting(self) -> None:
        """pydantic renders the raw invalid input inline (``input_value=...``).

        ``report_exception`` logs with ``exc_info``, which bypasses the Sentry
        ``before_send`` scrubber entirely (that hook only sees the Sentry
        event, not the local log line) — so the swap must happen before the
        exception is ever logged, not just before it's captured.
        """
        secret_value = "leaked-secret-token"
        mock_log = _mock_logger()
        try:
            _SecretConfig.model_validate({"api_token": secret_value, "port": "not-a-number"})
        except ValidationError as exc:
            validation_error = exc
        else:
            raise AssertionError("expected ValidationError")

        with patch("infrastructure.observability.errors.boundary.capture_exception") as mock_cap:
            report_classify_failure(
                validation_error,
                logger=mock_log,
                integration="widget",
                record_id="rec-1",
            )

        logged_exc = mock_log.warning.call_args.kwargs["exc_info"]
        assert not isinstance(logged_exc, ValidationError)
        assert secret_value not in str(logged_exc)
        assert str(logged_exc) == "widget config validation failed"

        captured_exc = mock_cap.call_args[0][0]
        assert not isinstance(captured_exc, ValidationError)
        assert secret_value not in str(captured_exc)

    def test_validation_error_wrapper_keeps_original_traceback(self) -> None:
        """The wrapper must not drop the traceback pointing at the failing
        vendor model/validator, or logs/Sentry become undebuggable — but it
        must not use ``raise ... from`` chaining either, since that would
        print the raw ``ValidationError`` text (with the secret) as part of
        the exception chain when the local log formats it.
        """
        mock_log = _mock_logger()
        try:
            _SecretConfig.model_validate({"api_token": "x", "port": "not-a-number"})
        except ValidationError as exc:
            validation_error = exc
        else:
            raise AssertionError("expected ValidationError")

        with patch("infrastructure.observability.errors.boundary.capture_exception"):
            report_classify_failure(
                validation_error,
                logger=mock_log,
                integration="widget",
                record_id="rec-1",
            )

        logged_exc = mock_log.warning.call_args.kwargs["exc_info"]
        assert logged_exc.__traceback__ is validation_error.__traceback__
        assert logged_exc.__cause__ is None
        assert logged_exc.__context__ is None

    def test_non_validation_error_passes_through_unchanged(self) -> None:
        mock_log = _mock_logger()
        exc = RuntimeError("boom")
        with patch("infrastructure.observability.errors.boundary.capture_exception") as mock_cap:
            report_classify_failure(
                exc,
                logger=mock_log,
                integration="widget",
                record_id="rec-1",
            )
        assert mock_log.warning.call_args.kwargs["exc_info"] is exc
        assert mock_cap.call_args[0][0] is exc

    def test_tags_have_expected_shape(self) -> None:
        mock_log = _mock_logger()
        with patch("infrastructure.observability.errors.boundary.capture_exception") as mock_cap:
            report_classify_failure(
                RuntimeError("boom"),
                logger=mock_log,
                integration="widget",
                record_id="rec-1",
            )
        extra = mock_cap.call_args[1]["extra"]
        assert extra["tag.surface"] == "integration"
        assert extra["tag.component"] == "integrations"
        assert extra["tag.integration"] == "widget"
        assert extra["tag.event"] == "classify_failed"
        assert extra["record_id"] == "rec-1"
