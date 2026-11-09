"""Unit tests for core.tool.execution (report_run_error helper)."""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import BaseModel, ValidationError, model_validator

from core.tool.execution import report_run_error

# ---------------------------------------------------------------------------
# Fixture: captured Sentry events
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_sentry_events(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[Any]]:
    """Patch the Sentry SDK so every capture lands in a local list.

    Re-enables the Sentry path (which conftest disables globally) and
    installs a recording ``push_scope`` / ``capture_exception`` pair so
    tests can assert on the tags that reached Sentry without making real
    network calls.
    """
    monkeypatch.delenv("OPENSRE_SENTRY_DISABLED", raising=False)
    monkeypatch.delenv("OPENSRE_NO_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)

    events: list[Any] = []
    scope_stack: list[Any] = []

    class _RecordingScope:
        def __init__(self) -> None:
            self.extras: dict[str, Any] = {}

        def __enter__(self) -> _RecordingScope:
            scope_stack.append(self)
            return self

        def __exit__(self, *_args: object) -> None:
            if scope_stack and scope_stack[-1] is self:
                scope_stack.pop()

        def set_tag(self, key: str, value: str) -> None:
            self.extras[f"tag.{key}"] = value

        def set_extra(self, key: str, value: object) -> None:
            self.extras[key] = value

    class _CapturedEvent:
        def __init__(self, exc: BaseException, extras: dict[str, Any]) -> None:
            self.exc = exc
            self.extras = extras

    def _capture(exc: BaseException) -> None:
        current_extras = dict(scope_stack[-1].extras) if scope_stack else {}
        events.append(_CapturedEvent(exc=exc, extras=current_extras))

    monkeypatch.setitem(
        sys.modules,
        "sentry_sdk",
        SimpleNamespace(capture_exception=_capture, push_scope=_RecordingScope),
    )
    yield events


# ---------------------------------------------------------------------------
# report_run_error — direct helper tests
# ---------------------------------------------------------------------------


def test_report_run_error_captures_with_expected_tags(
    captured_sentry_events: list[Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    boom = RuntimeError("boom")
    with caplog.at_level(logging.ERROR, logger="tools"):
        report_run_error(
            boom,
            tool_name="query_azure_monitor_logs",
            source="azure",
            component="integrations.azure.tools.azure_monitor_logs_tool",
            method="httpx.post",
            extras={"workspace_id": "w"},
        )

    assert len(captured_sentry_events) == 1
    event = captured_sentry_events[0]
    assert event.exc is boom
    assert event.extras["tag.surface"] == "tool"
    assert event.extras["tag.tool_name"] == "query_azure_monitor_logs"
    assert event.extras["tag.source"] == "azure"
    assert event.extras["tag.component"] == "integrations.azure.tools.azure_monitor_logs_tool"
    assert event.extras["tag.method"] == "httpx.post"
    assert event.extras["workspace_id"] == "w"
    assert "Tool query_azure_monitor_logs failed" in caplog.text


def test_report_run_error_supports_warning_severity(
    captured_sentry_events: list[Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    err = RuntimeError("recoverable")
    with caplog.at_level(logging.WARNING, logger="tools"):
        report_run_error(
            err,
            tool_name="describe_eks_cluster",
            source="eks",
            component="integrations.eks.tools",
            severity="warning",
        )

    assert len(captured_sentry_events) == 1
    assert captured_sentry_events[0].exc is err
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records == [], "warning severity must not log at error level"
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records, "warning severity must produce a WARNING log record"


def _handshake_timeout_wrapped_by_a_tool_error() -> Exception:
    """The shape a client raises: its own error from httpx's ConnectTimeout."""
    request = httpx.Request("POST", "https://app.test/api/agent-backend/gateway/prompts")
    try:
        try:
            raise httpx.ConnectTimeout("The handshake operation timed out", request=request)
        except httpx.ConnectTimeout as timeout:
            raise RuntimeError("unreachable") from timeout
    except RuntimeError as wrapped:
        return wrapped


def test_report_run_error_for_an_unreachable_service_is_one_warning_without_a_stack(
    captured_sentry_events: list[Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The shell prints ERROR records; a transient timeout must not dump the HTTP stack there.

    Regression: one hosted-gateway handshake timeout put ~90 lines of httpx and
    httpcore frames between the shell's progress lines.
    """
    with caplog.at_level(logging.DEBUG, logger="tools"):
        report_run_error(
            _handshake_timeout_wrapped_by_a_tool_error(),
            tool_name="ask_hosted_gateway",
            source="opensre",
            component="integrations.hosted_gateway.tools.gateway_prompt.ask_hosted_gateway",
        )

    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.WARNING, "Tool ask_hosted_gateway failed: RuntimeError")
    ]
    assert "Traceback" not in caplog.text
    assert len(captured_sentry_events) == 1, "an outage of the app must stay visible in Sentry"


class _SecretToolConfig(BaseModel):
    api_key: str

    @model_validator(mode="after")
    def _reject(self) -> _SecretToolConfig:
        # A model-level (not field-level) failure renders the *whole* input
        # mapping via `input_value=...`, which is where a secret escapes.
        raise ValueError("api_key rejected by policy")


def test_report_run_error_validation_error_does_not_echo_secret(
    captured_sentry_events: list[Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A model-level ValidationError's ``input_value`` can carry a secret field value.

    ``message`` here is caller-built (``f"Tool {tool_name} failed: ..."``) and
    must not interpolate ``str(exc)`` itself, or the report_exception scrub
    (keyed off the exception, not the caller's message string) never runs on
    the actual leaking text. Regression for a Greptile P1 finding on the
    argocd/jira client-factory soft-fail-logging PR.
    """
    secret_api_key = "s3cr3t-tool-api-key"
    try:
        _SecretToolConfig.model_validate({"api_key": secret_api_key})
    except ValidationError as exc:
        validation_error: BaseException = exc
    else:
        raise AssertionError("expected ValidationError")

    with caplog.at_level(logging.ERROR, logger="tools"):
        report_run_error(
            validation_error,
            tool_name="configure_widget",
            source="widget",
            component="integrations.widget.tools",
        )

    assert secret_api_key not in caplog.text
    assert "input_value" not in caplog.text

    assert len(captured_sentry_events) == 1
    captured_exc = captured_sentry_events[0].exc
    assert not isinstance(captured_exc, ValidationError)
    assert secret_api_key not in str(captured_exc)
    assert "input_value" not in str(captured_exc)


def test_report_run_error_uses_provided_logger(
    captured_sentry_events: list[Any],
) -> None:
    custom_logger = MagicMock(spec=logging.Logger)
    err = ValueError("nope")

    report_run_error(
        err,
        tool_name="list_eks_pods",
        source="eks",
        component="integrations.eks.tools",
        logger=custom_logger,
    )

    custom_logger.error.assert_called_once()
    assert len(captured_sentry_events) == 1
    assert captured_sentry_events[0].exc is err
